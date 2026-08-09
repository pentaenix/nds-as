"""Shared Qt worker-thread lifecycle helpers."""
from __future__ import annotations

from PySide6.QtCore import QThread


def stop_qthread(worker: QThread | None, *, wait_ms: int = 60_000) -> None:
    if worker is None:
        return
    if not worker.isRunning():
        return
    worker.requestInterruption()
    if not worker.wait(wait_ms):
        worker.terminate()
        worker.wait(5_000)


def launch_qthread(window: object, attr: str, worker: QThread) -> QThread:
    """Stop any previous worker stored on *window*, parent the new one, and track it."""
    stop_qthread(getattr(window, attr, None))
    worker.setParent(window)

    def clear() -> None:
        if getattr(window, attr, None) is worker:
            setattr(window, attr, None)

    worker.finished.connect(clear)
    setattr(window, attr, worker)
    return worker
