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
from ...platforms import active_platforms, platform_for_path
from ...model_texture_resolver import resolve_model_textures, write_resolution_images
from ...nds import NDSRom
from ...nitro_2d import decode_nitro2d_preview, decode_nitro2d_related_preview, save_preview_images
from ...nitro_names import asset_browser_name, asset_filename_label, extract_nitro_names, texture_match_report
from ...nitro_textures import decode_btx_images, decode_guided_tex0_images, make_contact_sheet, parse_tex0_manifest, save_decoded_images
from ...profiles import detect_profile
from ...scanner import Asset, asset_search_text, filter_assets, filter_assets_by_types, filter_assets_indexed, scan_nds_path
from ...virtual_texture_assets import expand_btx0_texture_slots
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
from ..recent_roms import load_recent_roms, remember_recent_rom
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

class RomLoaderMixin:
    def _roms_directory(self) -> Path:
        roms = project_root() / "roms"
        roms.mkdir(exist_ok=True)
        return roms

    def _rom_open_filter(self) -> str:
        parts = []
        for p in active_platforms():
            exts = " ".join(f"*{ext}" for ext in p.rom_extensions)
            parts.append(f"{p.label} ({exts})")
        parts.append("All files (*.*)")
        return ";;".join(parts)

    def open_rom_path(self, path: str) -> None:
        self._open_rom_path(path)

    def open_rom(self) -> None:
        roms_dir = self._roms_directory()
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open ROM",
            str(roms_dir),
            self._rom_open_filter(),
        )
        if not path:
            return
        self._open_rom_path(path)

    def _rebuild_recent_roms_menu(self) -> None:
        menu = getattr(self, "_recent_roms_menu", None)
        button = getattr(self, "open_recent_rom_toolbar_button", None)
        if menu is None or button is None:
            return
        menu.clear()
        recent = load_recent_roms()
        if not recent:
            placeholder = QAction("No recent ROMs", self)
            placeholder.setEnabled(False)
            menu.addAction(placeholder)
            button.setEnabled(False)
            return
        button.setEnabled(True)
        for path in recent:
            label = Path(path).name
            action = QAction(label, self)
            action.setToolTip(path)
            action.triggered.connect(lambda checked=False, rom_path=path: self._open_rom_path(rom_path))
            menu.addAction(action)

    def _open_rom_path(self, path: str) -> None:
        platform = platform_for_path(Path(path))
        if platform is None or platform.status != "active" or platform.scan_rom_path is None:
            QMessageBox.information(
                self,
                "Platform not supported yet",
                f"RAE does not scan {Path(path).suffix} files yet.\n\n"
                "Nintendo DS (.nds) is supported today. GBA, GB, GBC, and 3DS are planned — see src/platforms/.",
            )
            return
        self.rom_path = path
        from ...core.modules import PlatformDispatch

        self._rom_platform_id = PlatformDispatch.for_rom_path(path)
        self.assets = []
        self.visible_assets = []
        self._queued_preview_asset_id = None
        self._last_previewed_asset_id = None
        self._pinned_texture_asset_id = None
        self._clear_texture_caches()
        self._asset_search_text.clear()
        self._mapped_tree_parts_by_id.clear()
        self._raw_tree_parts_by_id.clear()
        self._browser_tab_versions.clear()
        self._filter_generation += 1
        self._name_cache.clear()
        self._display_name_cache.clear()
        self.current_mapping = None
        self.assets_by_id = {}
        self.session_path = None
        self.rom_game_code = ""
        self.rom_title = ""
        self.easyfind_path = None
        self._selected_asset_id = None
        self._selected_btx0_texture_name = None
        self._tree_group_rows = {}
        self._tree_loaded_groups = set()
        self._raw_tree_group_rows = {}
        self._raw_tree_loaded_groups = set()
        self.browser_page = 0
        if hasattr(self, "_update_main_toolbar"):
            self._update_main_toolbar()
        if hasattr(self, "preset_box"):
            self.preset_box.setCurrentIndex(0)
        self.table.setRowCount(0)
        if hasattr(self, "tree"):
            self.tree.clear()
        if hasattr(self, "raw_tree"):
            self.raw_tree.clear()
        self.details.clear()
        self.preview.clear()
        if hasattr(self, "_update_preview_inspector_visibility"):
            self._update_preview_inspector_visibility(None)
        self.preview.show_message("Opening ROM...\n\nRAE is building a guided asset index. Model conversion and audio expansion run only when you ask for them.")
        if hasattr(self, "sync_threeds_pokemon_bulk_export_ui"):
            from ..threeds_bulk_export import sync_threeds_pokemon_bulk_export_ui

            sync_threeds_pokemon_bulk_export_ui(self, visible=False)
        self._focus_terminal(
            banner="Opening ROM… watch this panel for scan and texture-index progress. The UI stays responsive while background workers run.",
        )
        mode = "exhaustive" if self.deep_scan_action.isChecked() else "guided"
        self._update_status(f"Scanning {path} in {mode} mode.")

        self.worker = ScanWorker(path, deep_scan=self.deep_scan_action.isChecked(), rom_platform_id=self._rom_platform_id)
        self.worker.progress.connect(self._update_status)
        self.worker.finished_ok.connect(self._scan_finished)
        self.worker.failed.connect(self._scan_failed)
        self.worker.start()

    def _scan_finished(self, assets: list[Asset], _report: object | None = None) -> None:
        if self.rom_path:
            remember_recent_rom(self.rom_path)
            self._rebuild_recent_roms_menu()
        self.assets = expand_btx0_texture_slots(assets)
        self.assets_by_id = {a.asset_id: a for a in self.assets}
        self._load_profile_summary()
        self._focus_terminal(banner=f"ROM scan complete: {len(assets):,} asset(s) found. Starting texture dictionary index before you preview models…")
        self._warm_texture_library_async()
        self._rebuild_asset_filter_indexes()
        self._rebuild_show_types_menu()
        # Apply the first filter pass synchronously so mapped/raw trees populate
        # immediately after large mobile scans (10k+ rows use a background worker
        # in apply_filter(), which left the browsers empty until it finished).
        generation = self._filter_generation
        self._apply_filter_result(generation, self._compute_filtered_assets())
        self._focus_browser_on_rom_folders()
        bmd_count = sum(1 for a in assets if a.magic == "BMD0")
        texture_count = sum(1 for a in assets if a.magic == "BTX0")
        tile_count = sum(1 for a in assets if a.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR"})
        png_count = sum(1 for a in assets if a.magic == "PNG")
        audio_count = sum(1 for a in assets if a.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"})
        self._populate_search_presets()
        mode = "exhaustive" if self.deep_scan_action.isChecked() else "guided"
        summary = self._session_overview_text(
            source_label=Path(self.rom_path).name if self.rom_path else "Open ROM",
            mode=mode,
            counts=(len(assets), bmd_count, texture_count, tile_count, png_count, audio_count),
        )
        self._update_status(f"Loaded {len(assets)} assets. Details tab has the full profile and counts.")
        self._update_status(self.profile_text or "No game-specific mapping summary was available.")
        if not self.table.selectionModel().selectedRows() and not self.tree.selectedItems():
            self.details.setPlainText(summary)
            self.preview.show_message("Choose an asset to preview or export. Models load with textures automatically when RAE can resolve them.")
        if hasattr(self, "_update_main_toolbar"):
            self._update_main_toolbar()
        if hasattr(self, "_bind_easyfind_for_current_game"):
            self._bind_easyfind_for_current_game(log=True)
        if hasattr(self, "sync_threeds_pokemon_bulk_export_ui"):
            from ..threeds_bulk_export import sync_threeds_pokemon_bulk_export_ui

            sync_threeds_pokemon_bulk_export_ui(self)

    def _session_overview_text(self, *, source_label: str, mode: str, counts: tuple[int, int, int, int, int, int]) -> str:
        total, models, textures, two_d, pngs, audio = counts
        mapping_label = self.current_mapping.mapping_id if self.current_mapping else "none"
        lines = [
            "Session overview",
            f"Source: {source_label}",
            f"Scan mode: {mode}",
            f"Mapping: {mapping_label}",
            "",
            "Detected assets",
            f"  Models: {models}",
            f"  Texture archives: {textures}",
            f"  2D graphics / tiles / fonts: {two_d}",
            f"  PNG images: {pngs}",
            f"  Audio / music / SFX: {audio}",
            f"  Total: {total}",
            "",
            "How to work efficiently",
            "  1. Use the mapped tree or filters to narrow the list.",
            "  2. Browse models with automatic texture preview; use Set Textures to force a refresh or after pinning a BTX0 manually.",
            "  3. Export Selected offers the options that make sense for the selected asset.",
            "  4. Save Session writes a self-contained .dsmsession file in saves/ so you can continue later without reopening the ROM.",
        ]
        if self.profile_text:
            lines.extend(["", "Game profile", self.profile_text])
        return "\n".join(lines)

    def _scan_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Scan failed", message)
        self._update_status("Scan failed.")
