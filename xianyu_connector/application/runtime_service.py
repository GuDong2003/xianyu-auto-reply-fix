from __future__ import annotations

from xianyu_connector.domain.account_state import (
    AccountReadiness,
    AccountRuntime,
    AccountState,
    find_transition_path,
    transition_account,
)
from xianyu_connector.infrastructure.sqlite_runtime_repository import SqliteRuntimeRepository


class RuntimeService:
    def __init__(self, repository: SqliteRuntimeRepository) -> None:
        self._repository = repository

    def transition_to(
        self,
        account_id: str,
        target: AccountState,
        *,
        reason_code: str | None = None,
        reason_message: str | None = None,
        clear_readiness: bool = False,
    ) -> AccountRuntime:
        runtime = self._repository.ensure(account_id)
        for state in find_transition_path(runtime.state, target):
            readiness = AccountReadiness() if clear_readiness else runtime.readiness
            current = transition_account(
                runtime,
                state,
                readiness=readiness,
                reason_code=reason_code if state is target else None,
                reason_message=reason_message if state is target else None,
            )
            self._repository.save_transition(runtime, current)
            runtime = current
        return runtime
