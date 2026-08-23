import sys
import time

from xianyu_connector.infrastructure.bounded_process import BoundedProcessRunner


def test_bounded_process_returns_output() -> None:
    result = BoundedProcessRunner().run(
        [sys.executable, "-c", "print('ok')"],
        timeout_seconds=2,
    )

    assert result.return_code == 0
    assert result.stdout.strip() == "ok"
    assert result.timed_out is False


def test_bounded_process_kills_hung_process_group() -> None:
    started_at = time.monotonic()

    result = BoundedProcessRunner().run(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        timeout_seconds=0.1,
        terminate_grace_seconds=0.1,
    )

    assert result.timed_out is True
    assert time.monotonic() - started_at < 2
