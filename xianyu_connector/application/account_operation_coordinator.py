from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass
from enum import StrEnum


class AccountOperationKind(StrEnum):
    QR = "qr"
    MANUAL_VERIFICATION = "manual_verification"


class AccountOperationConflict(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class AccountOperationLease:
    account_id: str
    kind: AccountOperationKind
    token: str


class _AccountEntry:
    def __init__(self) -> None:
        self.mutex = threading.Lock()
        self.lease: AccountOperationLease | None = None


class AccountOperationCoordinator:
    """Coordinates account-level mutations across async APIs and the sync supervisor."""

    def __init__(self) -> None:
        self._entries: dict[str, _AccountEntry] = {}
        self._entries_lock = threading.Lock()

    @contextmanager
    def hold(self, account_id: str, *, timeout_seconds: float | None = None) -> Iterator[None]:
        entry = self._entry(account_id)
        acquired = _acquire_lock(entry.mutex, timeout_seconds)
        if not acquired:
            raise AccountOperationConflict(f"account operation is busy: {account_id}")
        try:
            yield
        finally:
            entry.mutex.release()

    @asynccontextmanager
    async def hold_async(
        self,
        account_id: str,
        *,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[None]:
        entry = self._entry(account_id)
        deadline = (
            time.monotonic() + max(timeout_seconds, 0.0)
            if timeout_seconds is not None
            else None
        )
        while True:
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            if remaining == 0.0 and deadline is not None:
                acquired = False
            else:
                acquired = await asyncio.to_thread(
                    _acquire_lock,
                    entry.mutex,
                    min(0.1, remaining) if remaining is not None else 0.1,
                )
            if acquired:
                break
            if deadline is not None and time.monotonic() >= deadline:
                raise AccountOperationConflict(f"account operation is busy: {account_id}")
            await asyncio.sleep(0)
        try:
            yield
        finally:
            entry.mutex.release()

    def reserve(self, account_id: str, kind: AccountOperationKind) -> AccountOperationLease:
        entry = self._entry(account_id)
        with self._entries_lock:
            if entry.lease is not None:
                raise AccountOperationConflict(
                    f"{entry.lease.kind.value} operation is active for {account_id}"
                )
            lease = AccountOperationLease(account_id, kind, uuid.uuid4().hex)
            entry.lease = lease
            return lease

    async def reserve_async(
        self,
        account_id: str,
        kind: AccountOperationKind,
    ) -> AccountOperationLease:
        return await asyncio.to_thread(self.reserve, account_id, kind)

    def active_kind(self, account_id: str) -> AccountOperationKind | None:
        entry = self._entry(account_id)
        with self._entries_lock:
            return entry.lease.kind if entry.lease is not None else None

    def release(self, lease: AccountOperationLease) -> None:
        entry = self._entry(lease.account_id)
        with self._entries_lock:
            if entry.lease is None:
                return
            if entry.lease.token != lease.token:
                raise AccountOperationConflict("account operation lease mismatch")
            entry.lease = None

    async def release_async(self, lease: AccountOperationLease) -> None:
        await asyncio.to_thread(self.release, lease)

    def _entry(self, account_id: str) -> _AccountEntry:
        with self._entries_lock:
            return self._entries.setdefault(account_id, _AccountEntry())


def _acquire_lock(lock: threading.Lock, timeout_seconds: float | None) -> bool:
    if timeout_seconds is None:
        lock.acquire()
        return True
    return lock.acquire(timeout=max(timeout_seconds, 0.0))
