"""UI mixin module."""
from __future__ import annotations

import json
import os
import re
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

class SessionActionsMixin:
    def save_session(self) -> None:
        if not self.assets:
            QMessageBox.information(self, "Nothing to save", "Open a ROM or session before saving your work.")
            return
        if self.session_save_worker is not None and self.session_save_worker.isRunning():
            QMessageBox.information(self, "Session save running", "RAE is already saving a session. Progress is shown in Terminal.")
            self.info_tabs.setCurrentWidget(self.log_box)
            return
        saves_dir = Path.cwd() / "saves"
        saves_dir.mkdir(exist_ok=True)
        base_name = Path(self.rom_path).stem if self.rom_path else "dsm_session"
        safe_base = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in base_name)[:64] or "dsm_session"
        suggested = saves_dir / f"{safe_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dsmsession"
        target, _ = QFileDialog.getSaveFileName(self, "Save RAE session", str(suggested), "RAE session (*.dsmsession);;Zip archive (*.zip)")
        if not target:
            return
        mapping_id = self.current_mapping.mapping_id if self.current_mapping else ""
        self.info_tabs.setCurrentWidget(self.log_box)
        self._update_status("Saving session. This writes extracted asset data into saves/ and may take a moment for large ROMs.")
        self.session_save_worker = SessionSaveWorker(
            Path(target),
            self.assets,
            rom_path=self.rom_path,
            profile_text=self.profile_text,
            mapping_id=mapping_id,
            pinned_texture_asset_id=self._pinned_texture_asset_id,
            texture_assignments=self._texture_assignments,
            texture_sequences=getattr(self, "_texture_sequences", {}),
        )
        self.session_save_worker.progress.connect(self._update_status)
        self.session_save_worker.finished_ok.connect(self._session_save_finished)
        self.session_save_worker.failed.connect(self._session_save_failed)
        self.session_save_worker.start()

    def _session_save_finished(self, path: str) -> None:
        self.session_path = path
        QMessageBox.information(self, "Session saved", f"Saved RAE session:\n{path}")
        self._update_status(f"Session saved: {path}")

    def _session_save_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Session save failed", message)
        self._update_status(f"Session save failed: {message}")

    def open_session(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Open RAE session", str(Path.cwd() / "saves"), "RAE session (*.dsmsession *.zip);;All files (*.*)")
        if not source:
            return
        if self.session_load_worker is not None and self.session_load_worker.isRunning():
            QMessageBox.information(self, "Session load running", "RAE is already opening a session. Progress is shown in Terminal.")
            self.info_tabs.setCurrentWidget(self.log_box)
            return
        self._focus_terminal(banner=f"Opening saved session {source}…")
        self.preview.show_message("Opening saved session...\n\nRAE will restore the asset index without reading the original ROM.")
        self._update_status(f"Opening session {source}")
        self.session_load_worker = SessionLoadWorker(Path(source))
        self.session_load_worker.progress.connect(self._update_status)
        self.session_load_worker.finished_ok.connect(self._session_load_finished)
        self.session_load_worker.failed.connect(self._session_load_failed)
        self.session_load_worker.start()

    def _session_load_finished(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        self._clear_texture_caches()
        self.rom_path = None
        self.session_path = str(data.get("path", ""))
        self.assets = list(data.get("assets", []))
        self.assets_by_id = {a.asset_id: a for a in self.assets}
        manifest = data.get("manifest", {}) if isinstance(data.get("manifest"), dict) else {}
        self.profile_text = str(manifest.get("profile_text", ""))
        self._pinned_texture_asset_id = manifest.get("pinned_texture_asset_id") or None
        from ...core.texture_assignments import load_texture_assignments
        from ...core.texture_sequences import load_texture_sequences

        self._texture_assignments = load_texture_assignments(manifest)
        self._texture_sequences = load_texture_sequences(manifest)
        self.current_mapping = None
        self._selected_asset_id = None
        self._selected_btx0_texture_name = None
        self._queued_preview_asset_id = None
        self._last_previewed_asset_id = None
        self._name_cache.clear()
        self._display_name_cache.clear()
        self._tree_group_rows = {}
        self._tree_loaded_groups = set()
        self._raw_tree_group_rows = {}
        self._raw_tree_loaded_groups = set()
        self.browser_page = 0
        self.table.setRowCount(0)
        self.tree.clear()
        self._rebuild_asset_filter_indexes()
        self._rebuild_show_types_menu()
        self._focus_terminal(banner=f"Session loaded: {len(self.assets):,} asset(s). Building texture dictionary index before model preview…")
        self._warm_texture_library_async()
        self.apply_filter()
        self._focus_browser_on_rom_folders()
        total = len(self.assets)
        bmd_count = sum(1 for a in self.assets if a.magic == "BMD0")
        texture_count = sum(1 for a in self.assets if a.magic == "BTX0")
        tile_count = sum(1 for a in self.assets if a.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR"})
        png_count = sum(1 for a in self.assets if a.magic == "PNG")
        audio_count = sum(1 for a in self.assets if a.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"})
        overview = self._session_overview_text(
            source_label=Path(self.session_path).name if self.session_path else "Saved session",
            mode="session",
            counts=(total, bmd_count, texture_count, tile_count, png_count, audio_count),
        )
        self.details.setPlainText(overview)
        self.preview.show_message("Session loaded. Select an asset to preview or export selected data.")
        self._update_status(f"Session loaded: {total} assets restored. Original ROM is not required for this session.")

    def _session_load_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Session load failed", message)
        self._update_status(f"Session load failed: {message}")

