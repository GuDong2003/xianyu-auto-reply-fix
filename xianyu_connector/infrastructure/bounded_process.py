from __future__ import annotations

import os
import signal
import subprocess  # nosec B404
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProcessResult:
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool


class BoundedProcessRunner:
    def run(
        self,
        command: Sequence[str],
        *,
        timeout_seconds: float,
        terminate_grace_seconds: float = 10,
        environment: Mapping[str, str] | None = None,
    ) -> ProcessResult:
        process = subprocess.Popen(  # nosec B603
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            env=dict(environment) if environment else None,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout_seconds)
            return ProcessResult(process.returncode, stdout, stderr, False)
        except subprocess.TimeoutExpired:
            self._terminate_group(process, terminate_grace_seconds)
            stdout, stderr = process.communicate()
            return ProcessResult(process.returncode, stdout, stderr, True)

    @staticmethod
    def _terminate_group(process: subprocess.Popen[str], grace_seconds: float) -> None:
        if process.poll() is not None:
            return
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
