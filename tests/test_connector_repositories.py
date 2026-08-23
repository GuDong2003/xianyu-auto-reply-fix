from datetime import UTC, datetime
from pathlib import Path

import pytest

from xianyu_connector.domain.account_state import (
    AccountRuntime,
    AccountState,
    transition_account,
)
from xianyu_connector.domain.commands import AccountCommand, CommandStatus
from xianyu_connector.infrastructure.schema import apply_connector_schema
from xianyu_connector.infrastructure.sqlite_command_repository import SqliteCommandRepository
from xianyu_connector.infrastructure.sqlite_runtime_repository import (
    RuntimeVersionConflict,
    SqliteRuntimeRepository,
)
from xianyu_connector.infrastructure.sqlite_secret_repository import SqliteSecretRepository
from xianyu_connector.security.aes_gcm import SecretCipher


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    path = tmp_path / "connector.db"
    import sqlite3

    with sqlite3.connect(path) as connection:
        apply_connector_schema(connection)
    return path


def test_runtime_transition_is_persisted_with_optimistic_lock(database_path: Path) -> None:
    repository = SqliteRuntimeRepository(database_path)
    initial = AccountRuntime(
        "account-1",
        state=AccountState.OFFLINE,
        entered_at=datetime(2026, 7, 17, tzinfo=UTC),
    )
    repository.save_initial(initial)
    pending = transition_account(initial, AccountState.QR_PENDING)

    repository.save_transition(initial, pending)

    assert repository.get("account-1") == pending
    with pytest.raises(RuntimeVersionConflict):
        repository.save_transition(initial, pending)


def test_command_idempotency_returns_original_command(database_path: Path) -> None:
    repository = SqliteCommandRepository(database_path)

    first = repository.enqueue("account-1", AccountCommand.START, "idem-1", {})
    second = repository.enqueue("account-1", AccountCommand.START, "idem-1", {})

    assert first.command_id == second.command_id
    assert repository.claim_next().status is CommandStatus.RUNNING
    assert repository.claim_next() is None


def test_command_idempotency_is_scoped_to_account(database_path: Path) -> None:
    repository = SqliteCommandRepository(database_path)

    first = repository.enqueue("account-1", AccountCommand.START, "shared-key", {})
    second = repository.enqueue("account-2", AccountCommand.START, "shared-key", {})

    assert first.command_id != second.command_id
    assert first.account_id == "account-1"
    assert second.account_id == "account-2"


def test_cancelled_running_command_cannot_be_overwritten_by_late_completion(
    database_path: Path,
) -> None:
    repository = SqliteCommandRepository(database_path)
    queued = repository.enqueue("account-1", AccountCommand.START, "cancel-cas", {})
    running = repository.claim_next()
    assert running is not None
    assert running.command_id == queued.command_id

    assert repository.cancel(running.command_id, error_message="cancelled") is True
    assert repository.complete(running.command_id, result={"state": "online"}) is False

    current = repository.get(running.command_id)
    assert current is not None
    assert current.status is CommandStatus.FAILED
    assert current.error_message == "cancelled"


def test_command_completion_is_compare_and_set_from_running(database_path: Path) -> None:
    repository = SqliteCommandRepository(database_path)
    queued = repository.enqueue("account-1", AccountCommand.START, "complete-cas", {})

    assert repository.complete(queued.command_id, result={"state": "online"}) is False
    running = repository.claim_next()
    assert running is not None
    assert repository.complete(running.command_id, result={"state": "online"}) is True
    assert repository.complete(running.command_id, result={"state": "duplicate"}) is False


def test_interrupted_commands_are_requeued_after_restart(database_path: Path) -> None:
    repository = SqliteCommandRepository(database_path)
    queued = repository.enqueue("account-1", AccountCommand.START, "idem-recover", {})
    assert repository.claim_next().status is CommandStatus.RUNNING

    assert repository.recover_interrupted() == 1
    recovered = repository.claim_next()

    assert recovered.command_id == queued.command_id
    assert recovered.status is CommandStatus.RUNNING


def test_secret_repository_binds_ciphertext_to_account(database_path: Path) -> None:
    repository = SqliteSecretRepository(database_path, SecretCipher(b"k" * 32))

    repository.save("account-1", "cookie", "cookie2=secret")

    assert repository.get("account-1", "cookie") == "cookie2=secret"
    assert repository.get("account-2", "cookie") is None
