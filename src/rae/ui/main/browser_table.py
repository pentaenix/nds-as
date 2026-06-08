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

class BrowserTableMixin:
    def _asset_type_label(self, asset: Asset) -> str:
        return TYPE_LABELS.get(asset.magic, asset.magic or asset.kind or "unknown")

    def _browser_row_values(self, asset: Asset) -> list[str]:
        return [
            self._asset_display_name(asset),
            self._asset_file_label(asset),
            self._asset_type_label(asset),
            asset.virtual_path,
        ]

    def _update_pagination_bar(self) -> None:
        if not hasattr(self, "page_label"):
            return
        on_flat = hasattr(self, "browser_tabs") and self.browser_tabs.currentWidget() is self.table
        self.pagination_row.setVisible(on_flat)
        if not on_flat:
            return
        total = len(self.visible_assets)
        page_size = self.page_size
        page_count = max(1, (total + page_size - 1) // page_size) if total else 1
        page = min(self.browser_page, page_count - 1)
        self.browser_page = page
        start = page * page_size + (1 if total else 0)
        end = min(total, (page + 1) * page_size)
        self.page_label.setText(f"Page {page + 1} of {page_count}")
        if total:
            self.page_summary.setText(f"Showing {start}–{end} of {total:,}")
        else:
            self.page_summary.setText("No matching assets")
        self.page_prev_button.setEnabled(page > 0)
        self.page_next_button.setEnabled(total > 0 and page + 1 < page_count)

    def _change_browser_page(self, delta: int) -> None:
        if not hasattr(self, "browser_tabs") or self.browser_tabs.currentWidget() is not self.table:
            return
        total = len(self.visible_assets)
        page_count = max(1, (total + self.page_size - 1) // self.page_size) if total else 1
        new_page = max(0, min(self.browser_page + delta, page_count - 1))
        if new_page == self.browser_page:
            return
        self.browser_page = new_page
        self._populate_table(self.visible_assets)
        self._update_pagination_bar()

    def _on_browser_tab_changed(self, _index: int) -> None:
        self._populate_current_browser_tab()
        self._update_pagination_bar()

    def _populate_table(self, assets: list[Asset]) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        total = len(assets)
        page_count = max(1, (total + self.page_size - 1) // self.page_size) if total else 1
        self.browser_page = min(self.browser_page, page_count - 1)
        start = self.browser_page * self.page_size
        display_assets = assets[start:start + self.page_size]
        self.table.setRowCount(len(display_assets))
        for row, asset in enumerate(display_assets):
            global_index = start + row + 1
            values = [
                str(global_index),
                *self._browser_row_values(asset),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                source_row = start + row
                item.setData(Qt.UserRole, source_row)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(True)
        self._update_pagination_bar()

