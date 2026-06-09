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

class SelectionMixin:
    def selected_folder(self) -> tuple[tuple[str, ...], bool] | None:
        if not hasattr(self, "browser_tabs"):
            return None
        active_tree = self.browser_tabs.currentWidget()
        if active_tree not in {self.tree, self.raw_tree}:
            return None
        items = active_tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.UserRole)
        if isinstance(data, dict) and "folder" in data:
            return tuple(data["folder"]), bool(data.get("raw"))
        return None

    def _folder_assets(self, parts: tuple[str, ...], raw: bool) -> list[Asset]:
        rows = (self._raw_tree_group_rows if raw else self._tree_group_rows).get(parts, [])
        assets: list[Asset] = []
        for row in rows:
            if 0 <= row < len(self.visible_assets):
                assets.append(self.visible_assets[row])
        return assets

    def _asset_from_tree_item_data(self, data: object) -> Asset | None:
        if isinstance(data, dict):
            if data.get("placeholder"):
                self._selected_btx0_texture_name = None
                return None
        if isinstance(data, int) and 0 <= data < len(self.visible_assets):
            asset = self.visible_assets[data]
            self._selected_asset_id = asset.asset_id
            if asset.magic == "BTX0":
                self._selected_btx0_texture_name = self._preview_btx0_texture_name(asset)
            else:
                self._selected_btx0_texture_name = None
            return asset
        return None

    def selected_asset(self) -> Asset | None:
        # Prefer the currently active browser tab. Both the tree and flat list store
        # indexes into visible_assets in Qt.UserRole.
        if hasattr(self, "browser_tabs") and self.browser_tabs.currentWidget() in {self.tree, getattr(self, "raw_tree", None)}:
            active_tree = self.browser_tabs.currentWidget()
            items = active_tree.selectedItems() if active_tree is not None else []
            if items:
                asset = self._asset_from_tree_item_data(items[0].data(0, Qt.UserRole))
                if asset is not None:
                    return asset
        rows = self.table.selectionModel().selectedRows()
        if rows:
            visual_row = rows[0].row()
            item = self.table.item(visual_row, 0)
            if item is not None:
                source_row = item.data(Qt.UserRole)
                asset = self._asset_from_tree_item_data(source_row)
                if asset is not None:
                    return asset
        # Fallback to last selected asset id if a folder selection temporarily took focus.
        if self._selected_asset_id:
            return self.assets_by_id.get(self._selected_asset_id)
        return None

    def on_selection_changed(self) -> None:
        folder = self.selected_folder()
        if folder is not None:
            parts, raw = folder
            count = len(self._folder_assets(parts, raw))
            self.details.setPlainText(
                "\n".join([
                    f"Folder: {' / '.join(parts)}",
                    f"Assets: {count}",
                    "",
                    "Use Export… or File → Export Selected to export this folder as a ZIP.",
                ])
            )
            if hasattr(self, "export_button"):
                self.export_button.setEnabled(count > 0)
                self.export_button.setVisible(True)
            if hasattr(self, "reset_view_button"):
                self.reset_view_button.setEnabled(False)
            if hasattr(self, "preview_details"):
                self.preview_details.setPlainText(
                    f"Folder selected: {' / '.join(parts)}\n"
                    f"{count} asset(s). Use Export… to create a ZIP archive."
                )
            if hasattr(self, "_update_preview_inspector_visibility"):
                self._update_preview_inspector_visibility(None)
            return
        self.show_selected_details()
        if self.auto_preview_action.isChecked():
            self._schedule_auto_preview()

    def _schedule_auto_preview(self) -> None:
        if not hasattr(self, "preview_timer"):
            return
        self.preview_timer.start(250)

    def _auto_preview_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            return
        # Single-click preview is the default workflow. Expensive model conversion
        # runs in a worker thread and is queued if another preview is active.
        self.preview_selected_asset(manual=False, force=False)

