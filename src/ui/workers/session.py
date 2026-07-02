"""Session save/load background workers."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...scanner import Asset
from ...session import load_session_zip, save_session_zip

class SessionSaveWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(
        self,
        target: Path,
        assets: list[Asset],
        *,
        rom_path: str | None,
        profile_text: str,
        mapping_id: str,
        rom_game_code: str = "",
        rom_title: str = "",
        pinned_texture_asset_id: str | None,
        texture_assignments: dict[str, dict[str, str]] | None = None,
        texture_sequences: dict[str, dict] | None = None,
    ):
        super().__init__()
        self.target = target
        self.assets = list(assets)
        self.rom_path = rom_path
        self.rom_game_code = rom_game_code
        self.rom_title = rom_title
        self.profile_text = profile_text
        self.mapping_id = mapping_id
        self.pinned_texture_asset_id = pinned_texture_asset_id
        self.texture_assignments = dict(texture_assignments or {})
        self.texture_sequences = dict(texture_sequences or {})

    def run(self) -> None:
        try:
            written = save_session_zip(
                self.target,
                assets=self.assets,
                rom_path=self.rom_path,
                rom_game_code=self.rom_game_code,
                rom_title=self.rom_title,
                profile_text=self.profile_text,
                mapping_id=self.mapping_id,
                pinned_texture_asset_id=self.pinned_texture_asset_id,
                texture_assignments=self.texture_assignments,
                texture_sequences=self.texture_sequences,
                progress=self.progress.emit,
            )
            self.finished_ok.emit(str(written))
        except Exception as exc:
            self.failed.emit(str(exc))


class SessionLoadWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path):
        super().__init__()
        self.source = source

    def run(self) -> None:
        try:
            payload = load_session_zip(self.source, progress=self.progress.emit)
            self.finished_ok.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))

