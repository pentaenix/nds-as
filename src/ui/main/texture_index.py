"""UI mixin module."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ...asset_resolver import MODEL_ANIMATION_MAGICS, build_related_assets, folder_sibling_assets, pokemon_path_texture_candidates, texture_matches_for_model
from ...exporter import (
    apicula_available,
    apicula_help_text,
    archive_directory_as_zip,
    convert_texture_with_apicula,
    convert_with_apicula,
    converted_outputs,
    export_asset,
    export_assets,
    export_readable_asset,
    texture_outputs,
)
from ...install import project_root
from ...mapping import choose_mapping, mapping_summary
from ...model_texture_resolver import resolve_model_textures, write_resolution_images
from ...nds import NDSRom
from ...nitro_2d import decode_nitro2d_preview, decode_nitro2d_related_preview, save_preview_images
from ...nitro_names import asset_browser_name, asset_filename_label, extract_nitro_names, texture_match_report
from ...nitro_textures import decode_btx_images, decode_guided_tex0_images, make_contact_sheet, parse_tex0_manifest, save_decoded_images
from ...profiles import detect_profile
from ...scanner import Asset, asset_search_text, filter_assets, filter_assets_by_types, filter_assets_indexed, scan_nds_path
from ...session import load_session_zip, save_session_zip
from ...texture_library import TextureLibrary, TextureLibraryStore
from ...util import human_size
from ..constants import (
    BROWSER_COLUMNS,
    CHROME_BUTTON_STYLE,
    DEFAULT_TYPE_FILTER_ON,
    DROPDOWN_BUTTON_STYLE,
    FILTER_CHIP_STYLE,
    FLAT_COLUMNS,
    TYPE_FILTER_GROUPS,
    TYPE_FILTER_ORDER,
    TYPE_LABELS,
)
from ..preview_btx import write_btx_preview_images
from ..preview_quality import CachedTextureResolution, TextureQuality, converted_texture_quality
from ..preview_widgets import PreviewWidget, qcolor_rgbf
from ..workers import (
    FilterWorker,
    ImagePreviewWorker,
    PreviewWorker,
    ScanWorker,
    SessionLoadWorker,
    SessionSaveWorker,
    TextureLibraryWarmupWorker,
    TextureResolveWorker,
    TextureWorker,
)

class TextureIndexMixin:
    def _clear_texture_caches(self) -> None:
        if self.texture_warmup_worker is not None and self.texture_warmup_worker.isRunning():
            self.texture_warmup_worker.requestInterruption()
        self.texture_warmup_worker = None
        self._texture_library_store.clear()
        self._texture_resolution_cache.clear()
        self._btx0_texture_entries_cache.clear()

    def _btx0_texture_entries(self, asset: Asset) -> list[tuple[str, int, int, int]]:
        cached = self._btx0_texture_entries_cache.get(asset.asset_id)
        if cached is not None:
            return cached
        manifest = parse_tex0_manifest(asset.data)
        if not manifest or not manifest.textures:
            self._btx0_texture_entries_cache[asset.asset_id] = []
            return []
        entries = [(tex.name, tex.format_id, tex.width, tex.height) for tex in manifest.textures]
        self._btx0_texture_entries_cache[asset.asset_id] = entries
        return entries

    def _asset_file_index(self, asset: Asset) -> int | None:
        path = asset.virtual_path.replace("\\", "/")
        match = re.search(r"file_(\d+)", path, re.IGNORECASE)
        if match:
            return int(match.group(1))
        nums = re.findall(r"\d+", Path(path).name)
        return int(nums[-1]) if nums else None

    def _preview_btx0_texture_name(self, asset: Asset) -> str | None:
        """Pick a representative texture entry for browser preview (not always dictionary slot 0)."""
        from ...btx0_preview_selection import choose_btx0_thumbnail_texture_name

        return choose_btx0_thumbnail_texture_name(asset)

    def _primary_btx0_texture_name(self, asset: Asset) -> str | None:
        return self._preview_btx0_texture_name(asset)

    def _sync_btx0_texture_combo(self, asset: Asset) -> None:
        if not hasattr(self, "btx0_texture_combo"):
            return
        combo = self.btx0_texture_combo
        combo.blockSignals(True)
        combo.clear()
        entries = self._btx0_texture_entries(asset) if asset.magic == "BTX0" else []
        show_picker = asset.magic == "BTX0" and len(entries) > 1
        if hasattr(self, "btx0_texture_label"):
            self.btx0_texture_label.setVisible(show_picker)
        if show_picker:
            preview_name = self._preview_btx0_texture_name(asset)
            for name, fmt, width, height in entries:
                combo.addItem(f"{name} ({width}x{height}, fmt {fmt})", name)
            pick = preview_name or entries[0][0]
            index = combo.findData(pick)
            combo.setCurrentIndex(index if index >= 0 else 0)
            self._selected_btx0_texture_name = str(combo.currentData() or pick)
            combo.setVisible(True)
        else:
            combo.setVisible(False)
            if asset.magic == "BTX0" and entries:
                self._selected_btx0_texture_name = self._preview_btx0_texture_name(asset) or entries[0][0]
        combo.blockSignals(False)

    def _on_btx0_texture_combo_changed(self, index: int) -> None:
        if index < 0 or not hasattr(self, "btx0_texture_combo"):
            return
        name = self.btx0_texture_combo.itemData(index)
        if not name:
            return
        self._selected_btx0_texture_name = str(name)
        if self.auto_preview_action.isChecked():
            self._schedule_auto_preview()

    def _texture_library_for_session(self) -> TextureLibrary | None:
        if not self.assets:
            return None
        if self._texture_library_store.is_ready_for(self.assets):
            return self._texture_library_store.library
        return None

    def _sync_texture_library_store_context(self) -> None:
        if getattr(self, "session_path", None) and not getattr(self, "rom_path", None):
            scan_mode = "session"
        elif hasattr(self, "deep_scan_action") and self.deep_scan_action.isChecked():
            scan_mode = "deep"
        else:
            scan_mode = "fast"
        self._texture_library_store.set_context(
            game_code=getattr(self, "rom_game_code", ""),
            rom_path=getattr(self, "rom_path", None),
            scan_mode=scan_mode,
        )

    def _warm_texture_library_async(self) -> None:
        if not self.assets:
            return
        from ...core.modules import PlatformDispatch

        if not PlatformDispatch.supports_texture_library_warmup(
            rom_platform_id=getattr(self, "_rom_platform_id", None)
        ):
            return
        self._sync_texture_library_store_context()
        if self._texture_library_store.is_ready_for(self.assets):
            return
        if self.texture_warmup_worker is not None and self.texture_warmup_worker.isRunning():
            return
        count, _digest = self._texture_library_store.fingerprint(self.assets)
        self._focus_terminal(
            banner=(
                f"Building texture dictionary index for {count:,} BTX0/BMD0 archive(s) in the background. "
                "RAE is not frozen — model preview will be faster once this finishes."
            ),
        )
        self.texture_warmup_worker = TextureLibraryWarmupWorker(self.assets, self._texture_library_store)
        self.texture_warmup_worker.progress.connect(self._update_status)
        self.texture_warmup_worker.finished_ok.connect(self._texture_warmup_finished)
        self.texture_warmup_worker.failed.connect(self._texture_warmup_failed)
        self.texture_warmup_worker.start()

    def _texture_warmup_running(self) -> bool:
        return self.texture_warmup_worker is not None and self.texture_warmup_worker.isRunning()

    def _texture_warmup_finished(self) -> None:
        self._focus_terminal()
        self._update_status("Texture dictionary index is ready. Model preview and Set Textures can use exact NSBTX lookups immediately.")

    def _texture_warmup_failed(self, message: str) -> None:
        self._focus_terminal()
        self._update_status(f"Background texture indexing failed: {message}")

    def _texture_resolution_cache_key(
        self,
        asset_id: str,
        pinned_texture_asset_id: str | None,
        policy_key: str | None = None,
    ) -> str:
        count, digest = self._texture_library_store.fingerprint(self.assets)
        if policy_key is None:
            try:
                policy = self._model_preview_policy()
                policy_key = str(getattr(policy, "cache_key", ""))
            except Exception:
                policy_key = ""
        return f"v4:{policy_key}:{asset_id}:{pinned_texture_asset_id or ''}:{count}:{digest}"

    def _get_cached_texture_resolution(self, asset_id: str, *, policy_key: str | None = None) -> CachedTextureResolution | None:
        key = self._texture_resolution_cache_key(asset_id, self._pinned_texture_asset_id, policy_key)
        cached = self._texture_resolution_cache.get(key)
        if cached is None:
            return None
        if not cached.preview_path.exists():
            self._texture_resolution_cache.pop(key, None)
            return None
        return cached

    def _store_texture_resolution_cache(
        self,
        asset_id: str,
        *,
        report: str,
        selected_texture_id: str,
        preview_path: Path,
        auxiliary_paths: list[Path],
        texture_by_name: dict[str, Path] | None = None,
        material_to_texture: dict[str, str] | None = None,
        texture_bind_order: list[str] | None = None,
        policy_key: str | None = None,
    ) -> None:
        key = self._texture_resolution_cache_key(asset_id, self._pinned_texture_asset_id, policy_key)
        self._texture_resolution_cache[key] = CachedTextureResolution(
            report=report,
            selected_texture_id=selected_texture_id,
            preview_path=preview_path,
            auxiliary_paths=list(auxiliary_paths),
            texture_by_name={name: str(path) for name, path in (texture_by_name or {}).items()},
            material_to_texture=dict(material_to_texture or {}),
            texture_bind_order=list(texture_bind_order or []),
        )

