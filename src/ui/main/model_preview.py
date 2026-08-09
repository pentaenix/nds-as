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
from ..preview_quality import CachedTextureResolution, TextureQuality, best_preview_path, converted_texture_quality
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

class ModelPreviewMixin:
    def convert_preview_selected(self, *, manual: bool = True, force: bool = False) -> None:
        asset = self.selected_asset()
        if not asset:
            if manual:
                QMessageBox.information(self, "No selection", "Select a model asset first.")
            return
        self._preview_model_with_textures(asset, manual=manual, force=force, switch_to_details=False)

    def _start_geometry_preview(self, asset: Asset, *, manual: bool) -> None:
        from ...core.modules import get_platform_modules

        mobile_model = get_platform_modules("mobile").model
        stub = mobile_model.home_package_stub_message(asset)
        if stub:
            self.preview.show_message(stub)
            self._update_status("HOME package inventory loaded; use Device Toolkit → Mobile → Preview for viewport export.")
            return
        if asset.magic != "BMD0":
            if manual:
                QMessageBox.information(self, "Not a model", "Preview/conversion works best on BMD0 model assets. Select a BMD0 row.")
            else:
                self.preview.show_message("Selected row is not a BMD0 model. Filter for BMD0 to preview models.")
            return
        if not apicula_available():
            if manual:
                QMessageBox.warning(self, "apicula not found", apicula_help_text())
            else:
                self.preview.show_message(apicula_help_text())
                self._update_status("apicula was not found, so RAE can list/export but not preview models yet.")
            return

        out_dir = self.preview_temp / asset.asset_id / "geometry_preview"
        cached = converted_outputs(out_dir)
        if cached:
            preview_path = best_preview_path(cached) or cached[0]
            self._load_model_preview_glb(
                preview_path,
                asset_id=asset.asset_id,
                fallback_textures=[],
                texture_by_name={},
                material_to_texture={},
                texture_bind_order=[],
            )
            self._last_previewed_asset_id = asset.asset_id
            self._preview_fallback_count_by_asset_id[asset.asset_id] = 0
            self._preview_status_by_asset_id[asset.asset_id] = self._preview_result_text(preview_path, [])
            self._update_status(f"Previewing geometry-only fallback: {preview_path}")
            self._update_preview_details(asset)
            return

        if self.preview_worker is not None and self.preview_worker.isRunning():
            self._queued_preview_asset_id = asset.asset_id
            self._update_status(f"Queued geometry preview for {asset.virtual_path}...")
            return

        self.preview.show_message(
            f"Preparing geometry-only preview...\n{asset.virtual_path}\n\n"
            "Texture resolution did not produce a textured preview for this model."
        )
        self._update_status(f"Starting geometry-only preview for {asset.virtual_path}...")
        self.preview_worker = PreviewWorker(asset, out_dir, self.assets, self._pinned_texture_asset_id)
        self.preview_worker.progress.connect(self._update_status)
        self.preview_worker.finished_ok.connect(self._preview_finished)
        self.preview_worker.failed.connect(self._preview_failed)
        self.preview_worker.start()

    def _preview_finished(self, asset_id: str, result: object) -> None:
        asset = self.selected_asset()
        if asset and asset.asset_id == asset_id and getattr(result, "output_files", None):
            first = best_preview_path(result.output_files) or result.output_files[0]
            self._load_model_preview_glb(
                first,
                asset_id=asset_id,
                fallback_textures=list(getattr(result, "auxiliary_files", [])),
                texture_by_name=dict(getattr(result, "texture_by_name", {}) or {}),
                material_to_texture=dict(getattr(result, "material_to_texture", {}) or {}),
                texture_bind_order=list(getattr(result, "texture_bind_order", []) or []),
            )
            self._last_previewed_asset_id = asset_id
            fallback_paths = list(getattr(result, "auxiliary_files", []))
            self._preview_fallback_count_by_asset_id[asset_id] = len(fallback_paths)
            status = self._preview_result_text(first, fallback_paths)
            self._preview_status_by_asset_id[asset_id] = status
            quality = converted_texture_quality(first)
            if quality.confident:
                self._update_status(f"Previewing {first}. Converted outputs are in {first.parent}; {quality.summary()}.")
            elif quality.weak_material_only:
                self._update_status(f"Previewing {first}. Geometry-only preview loaded.")
            else:
                self._update_status(f"Previewing {first}. Geometry-only preview loaded.")
            self._update_preview_details(asset)
        else:
            self._update_status("Conversion finished for a previously selected row.")
        self._finish_texture_preview_queue(asset_id)

    def _preview_failed(self, asset_id: str, message: str) -> None:
        current = self.selected_asset()
        if current and current.asset_id == asset_id:
            self.preview.show_message(f"Conversion failed:\n{message}")
        self._update_status("Conversion failed.")
        self._finish_texture_preview_queue(asset_id)

