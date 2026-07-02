"""EasyFind build background worker."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...easyfind import (
    EasyFindQuickOpen,
    EasyFindValidationReport,
    create_easyfind_document,
    load_easyfind,
    save_easyfind,
    validate_easyfind,
)
from ...easyfind.build_options import (
    BUILD_MODE_CATCHUP,
    BUILD_MODE_FULL,
    BUILD_MODE_TYPES,
    EasyFindBuildOptions,
)
from ...easyfind.build_previews import enrich_document_with_previews
from ...easyfind.usage import enrich_document_with_usage
from ...core.mapping import choose_mapping, load_mappings
from ...easyfind.store import read_all_preview_blobs
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
        options: EasyFindBuildOptions | None = None,
        web_snapshot=None,
    ) -> None:
        super().__init__()
        self.assets = list(assets)
        self.output_path = output_path
        self.rom_path = rom_path
        self.platform = platform
        self.rom_title = rom_title
        self.rom_game_code = rom_game_code
        self.options = options or EasyFindBuildOptions(mode=BUILD_MODE_FULL)
        self.web_snapshot = web_snapshot
        self._stage_index = 0
        self._stages = [
            "Creating EasyFind document…",
            "Extracting map usage links…",
            "Baking previews and color signatures…",
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
            existing_blobs: dict[str, bytes] = {}
            incremental = (
                self.options.mode in {BUILD_MODE_TYPES, BUILD_MODE_CATCHUP}
                and self.output_path.is_file()
            )

            if incremental:
                self._emit_progress("Creating EasyFind document…")
                self.progress.emit("Loading existing EasyFind index…")
                document = load_easyfind(self.output_path)
                existing_blobs = read_all_preview_blobs(self.output_path)
            else:
                self._emit_progress("Creating EasyFind document…")
                document = create_easyfind_document(
                    assets=self.assets,
                    rom_path=self.rom_path,
                    platform=self.platform,
                    rom_title=self.rom_title,
                    rom_game_code=self.rom_game_code,
                )

            if self.options.mode == BUILD_MODE_TYPES and not self.options.bake_node_kinds:
                self.failed.emit("Select at least one asset type to bake.")
                return

            mapping = choose_mapping(
                self.rom_title,
                self.rom_game_code,
                load_mappings(self.platform),
            )

            self._emit_progress("Extracting map usage links…")
            document = enrich_document_with_usage(
                document,
                self.assets,
                mapping,
                platform=self.platform,
                progress=lambda msg: self.progress.emit(msg),
            )

            self._emit_progress("Baking previews and color signatures…")

            def bake_progress(stage: str) -> None:
                self.progress.emit(stage)

            document, preview_blobs = enrich_document_with_previews(
                document,
                self.assets,
                options=self.options,
                existing_preview_blobs=existing_blobs,
                progress=bake_progress,
                web_snapshot=self.web_snapshot,
            )

            self._emit_progress("Writing .easyfind container…")

            def save_progress(stage: str) -> None:
                self.progress.emit(stage)

            written = save_easyfind(
                self.output_path,
                document,
                preview_blobs=preview_blobs,
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
