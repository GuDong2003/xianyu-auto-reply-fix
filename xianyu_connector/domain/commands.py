from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class AccountCommand(StrEnum):
    START = "start"
    STOP = "stop"
    RECONNECT = "reconnect"
    RELOGIN_QR = "relogin_qr"
    RESUME_AFTER_VERIFICATION = "resume_after_verification"


class CommandStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class CommandRecord:
    command_id: str
    account_id: str
    command: AccountCommand
    idempotency_key: str
    payload: dict[str, Any]
    status: CommandStatus = CommandStatus.QUEUED
    result: dict[str, Any] | None = None
    error_message: str | None = None
