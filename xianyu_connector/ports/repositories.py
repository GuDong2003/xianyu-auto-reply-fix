from __future__ import annotations

from typing import Protocol

from xianyu_connector.domain.account_state import AccountRuntime
from xianyu_connector.domain.commands import AccountCommand, CommandRecord


class RuntimeRepository(Protocol):
    def get(self, account_id: str) -> AccountRuntime | None: ...

    def save_transition(self, previous: AccountRuntime, current: AccountRuntime) -> None: ...


class CommandRepository(Protocol):
    def enqueue(
        self,
        account_id: str,
        command: AccountCommand,
        idempotency_key: str,
        payload: dict[str, object],
    ) -> CommandRecord: ...

    def claim_next(self) -> CommandRecord | None: ...


class SecretRepository(Protocol):
    def save(self, account_id: str, secret_type: str, plaintext: str) -> None: ...

    def get(self, account_id: str, secret_type: str) -> str | None: ...
