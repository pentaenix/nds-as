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

class AssetLabelsMixin:
    def _sibling_assets(self, asset: Asset) -> list[Asset]:
        # Used by manual bundle export.
        result = build_related_assets(
            asset,
            self.assets,
            pinned_texture_asset_id=self._pinned_texture_asset_id,
            progress=self._update_status,
            texture_library=self._texture_library_for_session(),
        )
        return result.assets

    def _texture_matches_for_model(self, asset: Asset, *, limit: int = 16):
        return texture_matches_for_model(asset, self.assets, limit=limit, progress=None)

    def _pokemon_path_texture_candidates(self, asset: Asset, *, limit: int = 24) -> list[Asset]:
        return pokemon_path_texture_candidates(asset, self.assets, limit=limit)

    def _apply_tree_folder_labels(self, item: QTreeWidgetItem, parts: tuple[str, ...], rows: list[int], *, raw: bool) -> None:
        count = len(rows)
        breadcrumb = " / ".join(parts)
        item.setText(0, parts[-1])
        item.setText(1, "")
        item.setText(2, f"{count} asset{'s' if count != 1 else ''}")
        item.setText(3, breadcrumb)
        item.setToolTip(0, breadcrumb)

    def _texture_slot_parent_asset(self, asset: Asset) -> Asset | None:
        if not getattr(asset, "is_texture_slot", False) or not asset.parent_asset_id:
            return None
        by_id = getattr(self, "assets_by_id", {})
        return by_id.get(asset.parent_asset_id)

    def _asset_display_name(self, asset: Asset) -> str:
        cached = self._display_name_cache.get(asset.asset_id)
        if cached is not None:
            return cached
        if getattr(asset, "is_texture_slot", False) and asset.texture_slot:
            label = asset.texture_slot
        else:
            label = asset_browser_name(asset)
        self._display_name_cache[asset.asset_id] = label
        return label

    def _asset_file_label(self, asset: Asset) -> str:
        parent = self._texture_slot_parent_asset(asset)
        if parent is not None:
            return asset_filename_label(parent.virtual_path)
        return asset_filename_label(asset.virtual_path.split("#", 1)[0])

    def _asset_type_label(self, asset: Asset) -> str:
        if getattr(asset, "is_texture_slot", False):
            return "Tex slot"
        return TYPE_LABELS.get(asset.magic, asset.magic or asset.kind or "unknown")

    def _asset_names(self, asset: Asset) -> set[str]:
        names = self._name_cache.get(asset.asset_id)
        if names is None:
            if asset.size > 8 * 1024 * 1024:
                self._update_status(f"Skipping synchronous name scan for large asset {asset.virtual_path} ({human_size(asset.size)}). Use Set Textures or Export Selected… to run focused matching in a worker.")
                names = set()
            else:
                names = extract_nitro_names(asset.data)
            self._name_cache[asset.asset_id] = names
        return names

