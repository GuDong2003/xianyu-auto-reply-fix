from __future__ import annotations

import os
import signal
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path


class WorkerExitCode:
    STOPPED = 0
    MANUAL_VERIFICATION = 20
    SESSION_EXPIRED = 21
    RISK_CHALLENGE = 22
    NETWORK_FAILURE = 23
    TOKEN_EXPIRED = 24
    FATAL = 30


@dataclass(slots=True)
class WorkerProcess:
    account_id: str
    process: subprocess.Popen[bytes]

    @property
    def pid(self) -> int:
        return self.process.pid

    def is_alive(self) -> bool:
        return self.process.poll() is None

    def return_code(self) -> int | None:
        return self.process.poll()

    def stop(self, grace_seconds: float = 10) -> None:
        if not self.is_alive():
            return
        os.killpg(self.process.pid, signal.SIGTERM)
        try:
            self.process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(self.process.pid, signal.SIGKILL)
            self.process.wait()


class WorkerProcessFactory:
    def __init__(
        self,
        database_path: Path,
        profiles_root: Path,
        master_key_path: Path,
        *,
        shadow_mode: bool,
    ) -> None:
        self._database_path = database_path
        self._profiles_root = profiles_root
        self._master_key_path = master_key_path
        self._shadow_mode = shadow_mode

    def start(self, account_id: str) -> WorkerProcess:
        environment = os.environ.copy()
        environment.update(
            {
                "DB_PATH": str(self._database_path),
                "XIANYU_PROFILES_ROOT": str(self._profiles_root),
                "XIANYU_MASTER_KEY_PATH": str(self._master_key_path),
                "XY_SLIDER_DRISSION_FALLBACK": "false",
                "XIANYU_CONNECTOR_MODE": "true",
                "XIANYU_EXTERNAL_CONNECTOR": "true",
                "XIANYU_SHADOW_MODE": str(self._shadow_mode).lower(),
            }
        )
        process = subprocess.Popen(  # nosec B603
            [sys.executable, "-m", "xianyu_connector.worker_main", account_id],
            start_new_session=True,
            env=environment,
        )
        return WorkerProcess(account_id, process)
