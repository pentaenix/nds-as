"""Background batched EasyFind preview blob loading."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...easyfind.store import EasyFindPreviewReader

_BATCH_SIZE = 48


class EasyFindPreviewBatchWorker(QThread):
    batch_ready = Signal(object)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(
        self,
        source: Path,
        preview_jobs: list[tuple[str, str]],
    ) -> None:
        super().__init__()
        self.source = source
        self.preview_jobs = list(preview_jobs)

    def run(self) -> None:
        try:
            with EasyFindPreviewReader(self.source) as reader:
                batch: dict[str, bytes] = {}
                for index, (node_id, preview_id) in enumerate(self.preview_jobs):
                    if self.isInterruptionRequested():
                        return
                    try:
                        batch[node_id] = reader.read(preview_id)
                    except Exception:
                        continue
                    if len(batch) >= _BATCH_SIZE or index == len(self.preview_jobs) - 1:
                        if batch:
                            self.batch_ready.emit(dict(batch))
                            batch = {}
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))
