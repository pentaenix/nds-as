"""ROM scan and filter background workers."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ...scanner import Asset, filter_assets_indexed, scan_nds_path
from ...texture_library import TextureLibraryStore

class FilterWorker(QThread):
    finished_ok = Signal(int, list)
    failed = Signal(int, str)

    def __init__(
        self,
        generation: int,
        assets: list[Asset],
        *,
        enabled_types: list[str],
        mapping_query: str,
        text_query: str,
        search_text_by_id: dict[str, str],
    ):
        super().__init__()
        self.generation = generation
        self.assets = assets
        self.enabled_types = list(enabled_types)
        self.mapping_query = mapping_query
        self.text_query = text_query
        self.search_text_by_id = search_text_by_id

    def run(self) -> None:
        try:
            assets = self.assets
            if self.enabled_types:
                allowed = frozenset(self.enabled_types)
                assets = [asset for asset in assets if asset.magic in allowed]
            if self.mapping_query:
                assets = filter_assets_indexed(assets, self.mapping_query, self.search_text_by_id)
            if self.text_query:
                assets = filter_assets_indexed(assets, self.text_query, self.search_text_by_id)
            self.finished_ok.emit(self.generation, assets)
        except Exception as exc:
            self.failed.emit(self.generation, str(exc))


class TextureLibraryWarmupWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, assets: list[Asset], store: TextureLibraryStore):
        super().__init__()
        self.assets = list(assets)
        self.store = store

    def run(self) -> None:
        try:
            if self.store.is_ready_for(self.assets):
                self.finished_ok.emit()
                return
            count, _digest = self.store.fingerprint(self.assets)
            self.progress.emit(f"Indexing texture dictionaries in background ({count:,} BTX0/BMD0 file(s))...")
            self.store.get_or_build(self.assets, progress=self.progress.emit)
            self.progress.emit(f"Texture dictionary index ready ({count:,} archive(s) indexed).")
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class ScanWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(list, object)
    failed = Signal(str)

    def __init__(self, rom_path: str, *, deep_scan: bool = False):
        super().__init__()
        self.rom_path = rom_path
        self.deep_scan = deep_scan

    def run(self) -> None:
        try:
            self.progress.emit("Fast scan: reading ROM filesystem and known containers only. Mapped tree leaves are skipped during load.")
            assets = scan_nds_path(
                self.rom_path,
                progress=self.progress.emit,
                carve_unknown_blobs=self.deep_scan,
                expand_audio_archives=False,
            )
            self.progress.emit(f"Fast scan complete: {len(assets)} detected asset(s). Building visible folders lazily in the UI.")
            self.finished_ok.emit(assets, None)
        except Exception as exc:
            self.failed.emit(str(exc))


