from __future__ import annotations

from file_log_collector import FileLogCollector


class _CompletedThread:
    def __init__(self, *, target, daemon: bool) -> None:
        self.target = target
        self.daemon = daemon

    def start(self) -> None:
        return None


def test_new_install_uses_writable_logs_directory(monkeypatch) -> None:
    collector = FileLogCollector.__new__(FileLogCollector)
    collector.log_file = None
    collector.last_position = 0
    monkeypatch.setattr("file_log_collector.os.path.exists", lambda _path: False)
    monkeypatch.setattr(collector, "setup_loguru_file_output", lambda: None)
    monkeypatch.setattr("file_log_collector.threading.Thread", _CompletedThread)

    collector.setup_file_monitoring()

    assert collector.log_file == "logs/realtime.log"
