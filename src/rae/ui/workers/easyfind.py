"""EasyFind build background worker."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...easyfind import (
    EasyFindQuickOpen,
    EasyFindValidationReport,
    create_easyfind_document,
    save_easyfind,
    validate_easyfind,
)
from ...scanner import Asset


@dataclass
class EasyFindBuildResult:
    path: Path
    quick_open: EasyFindQuickOpen
    validation: EasyFindValidationReport


class EasyFindBuildWorker(QThread):
    progress = Signal(str)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        assets: list[Asset],
        output_path: Path,
        rom_path: str | None,
        platform: str = "nds",
        rom_title: str = "",
        rom_game_code: str = "",
    ) -> None:
        super().__init__()
        self.assets = list(assets)
        self.output_path = output_path
        self.rom_path = rom_path
        self.platform = platform
        self.rom_title = rom_title
        self.rom_game_code = rom_game_code
        self._stage_index = 0
        self._stages = [
            "Creating EasyFind document…",
            "Writing .easyfind container…",
            "Validating EasyFind…",
            "Done.",
        ]

    def _emit_progress(self, message: str) -> None:
        if message in self._stages:
            idx = self._stages.index(message)
            self._stage_index = max(self._stage_index, idx)
        self.progress.emit(message)

    def run(self) -> None:
        try:
            self._emit_progress("Creating EasyFind document…")
            document = create_easyfind_document(
                assets=self.assets,
                rom_path=self.rom_path,
                platform=self.platform,
                rom_title=self.rom_title,
                rom_game_code=self.rom_game_code,
            )

            self._emit_progress("Writing .easyfind container…")

            def save_progress(stage: str) -> None:
                self.progress.emit(stage)

            written = save_easyfind(
                self.output_path,
                document,
                progress=save_progress,
            )

            self._emit_progress("Validating EasyFind…")
            validation = validate_easyfind(written)

            from ...easyfind import load_easyfind_quick_open

            quick_open = load_easyfind_quick_open(written)

            self._emit_progress("Done.")
            self.finished.emit(EasyFindBuildResult(
                path=written,
                quick_open=quick_open,
                validation=validation,
            ))
        except Exception as exc:
            self.failed.emit(str(exc))
