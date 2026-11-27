"""Track owned Playwright processes across shutdown and driver exit."""

import threading

import psutil
from loguru import logger


class PlaywrightProcessTree:
    def __init__(self, pid: int, user_id: str):
        self.pid = pid
        self._user_id = user_id
        self._lock = threading.Lock()
        self._root = None
        self._descendants = {}
        self._snapshot_complete = False
        try:
            self._root = psutil.Process(pid)
        except psutil.NoSuchProcess:
            pass  # 父进程已退出，不重新绑定这个可能被系统复用的 PID
        except (psutil.Error, OSError) as exc:
            logger.warning(f"【{user_id}】无法识别 Playwright 进程 {pid}: {exc}")

    def snapshot(self) -> None:
        with self._lock:
            self._snapshot_locked()

    def _snapshot_locked(self) -> None:
        if self._root is None:
            return
        try:
            if not self._root.is_running():
                return
            children = self._root.children(recursive=True)
            self._descendants.update((proc.pid, proc) for proc in children)
            if self._root.is_running():
                self._snapshot_complete = True
        except psutil.NoSuchProcess:
            pass  # 仍保留先前记录的子进程，不能据此判定已清理
        except (psutil.Error, OSError) as exc:
            self._snapshot_complete = False
            logger.warning(f"【{self._user_id}】无法枚举 Playwright 进程树 {self.pid}: {exc}")

    def force_cleanup(self, reason: str) -> bool:
        """Only report success after a known tree is gone; keep identities for retries."""
        if not self._lock.acquire(blocking=False):
            return False  # 同一会话的 worker 和看门狗不能同时操作进程树
        try:
            self._snapshot_locked()
            targets = list(reversed(self._descendants.values()))
            if self._root is not None:
                targets.append(self._root)
            alive = [proc for proc in targets if proc.is_running()]
            if alive:
                logger.warning(
                    f"【{self._user_id}】兜底清理 Playwright 进程树（{reason}）: {[proc.pid for proc in alive]}"
                )
                # psutil 的信号操作校验进程身份，避免按可能已复用的裸 PID 执行 taskkill。
                self._signal_processes(alive, "terminate")
                _, alive = psutil.wait_procs(alive, timeout=1.0)
                if alive:
                    self._signal_processes(alive, "kill")
                    _, alive = psutil.wait_procs(alive, timeout=0.5)
            if alive:
                logger.warning(f"【{self._user_id}】Playwright 清理后仍有残留: {[proc.pid for proc in alive]}")
            if not self._snapshot_complete:
                logger.warning(f"【{self._user_id}】缺少完整的进程树快照，无法确认 {self.pid} 已清理")
            return self._snapshot_complete and not alive
        except (psutil.Error, OSError) as exc:
            logger.warning(f"【{self._user_id}】Playwright 进程树 {self.pid} 清理失败: {exc}")
            return False
        finally:
            self._lock.release()

    def _signal_processes(self, processes, action: str) -> None:
        for proc in processes:
            try:
                getattr(proc, action)()
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError) as exc:
                logger.warning(f"【{self._user_id}】{action} Playwright 进程 {proc.pid} 失败: {exc}")
