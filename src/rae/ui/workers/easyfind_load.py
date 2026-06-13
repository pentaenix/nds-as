"""Background EasyFind document loader."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...easyfind import load_easyfind, validate_easyfind


class EasyFindLoadWorker(QThread):
    finished_ok = Signal(object, object)
    failed = Signal(str)

    def __init__(self, source: Path) -> None:
        super().__init__()
        self.source = source

    def run(self) -> None:
        try:
            validation = validate_easyfind(self.source, verify_preview_hashes=False)
            if not validation.ok:
                self.finished_ok.emit(None, validation)
                return
            document = load_easyfind(self.source)
            self.finished_ok.emit(document, validation)
        except Exception as exc:
            self.failed.emit(str(exc))
