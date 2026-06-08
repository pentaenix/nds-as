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

class TextureResolveMixin:
    def find_and_load_texture_for_selected_model(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BMD0/NSBMD model first.")
            return
        if asset.magic != "BMD0":
            QMessageBox.information(self, "Not a model", "Set Textures works on BMD0/NSBMD model assets. Select a model first.")
            return
        if not apicula_available():
            QMessageBox.warning(self, "apicula not found", apicula_help_text())
            return
        self._preview_model_with_textures(asset, manual=True, force=True, switch_to_details=True)

    def _apply_texture_resolution_cache(self, asset: Asset, cached: CachedTextureResolution, *, switch_to_details: bool) -> None:
        self._last_texture_resolve_report[asset.asset_id] = cached.report
        if cached.selected_texture_id:
            self._pinned_texture_asset_id = cached.selected_texture_id
        self._selected_asset_id = asset.asset_id
        self.preview.load_glb(cached.preview_path, fallback_textures=cached.auxiliary_paths)
        self._last_previewed_asset_id = asset.asset_id
        self._preview_fallback_count_by_asset_id[asset.asset_id] = len(cached.auxiliary_paths)
        self._preview_status_by_asset_id[asset.asset_id] = self._preview_result_text(cached.preview_path, cached.auxiliary_paths)
        self._update_status(f"Previewing cached textured model: {asset.virtual_path}")
        self._update_preview_details(asset)
        if switch_to_details:
            self.show_selected_details()
            self.info_tabs.setCurrentWidget(self.details)

    def _start_texture_resolve_worker(self, asset: Asset) -> None:
        out_dir = self.preview_temp / asset.asset_id / "texture_resolve"
        self.texture_resolve_worker = TextureResolveWorker(
            asset,
            out_dir,
            self.assets,
            self._pinned_texture_asset_id,
            texture_library=self._texture_library_for_session(),
            texture_store=self._texture_library_store,
        )
        self.texture_resolve_worker.progress.connect(self._update_status)
        self.texture_resolve_worker.finished_ok.connect(self._texture_resolve_finished)
        self.texture_resolve_worker.failed.connect(self._texture_resolve_failed)
        self.texture_resolve_worker.start()

    def _preview_model_with_textures(
        self,
        asset: Asset,
        *,
        manual: bool,
        force: bool,
        switch_to_details: bool = False,
    ) -> None:
        if asset.magic != "BMD0":
            if manual:
                QMessageBox.information(self, "Not a model", "Preview works on BMD0/NSBMD model assets.")
            return
        if not apicula_available():
            if manual:
                QMessageBox.warning(self, "apicula not found", apicula_help_text())
            else:
                self.preview.show_message(apicula_help_text())
                self._update_status("apicula was not found, so RAE can list/export but not preview models yet.")
            return

        if self.texture_resolve_worker is not None and self.texture_resolve_worker.isRunning():
            self._queued_preview_asset_id = asset.asset_id
            self._update_status(f"Queued textured preview for {asset.virtual_path}...")
            return

        if not force:
            cached = self._get_cached_texture_resolution(asset.asset_id)
            if cached is not None:
                self._apply_texture_resolution_cache(asset, cached, switch_to_details=switch_to_details)
                return

        self._selected_asset_id = asset.asset_id
        self._texture_preview_switch_to_details = switch_to_details
        if switch_to_details or self._texture_warmup_running():
            self._focus_terminal()
        pinned = self._pinned_texture_asset()
        pin_note = f"\nPinned texture: {pinned.virtual_path}" if pinned else ""
        warmup_note = (
            "\n\nTexture dictionary index is still building in Terminal. Preview will continue in the background."
            if self._texture_warmup_running()
            else ""
        )
        self.preview.show_message(
            f"Previewing model with textures...\n\n{asset.virtual_path}{pin_note}\n\n"
            "RAE is matching NSBMD materials to NSBTX dictionaries and converting the preview."
            f"{warmup_note}"
        )
        self._update_status(f"Previewing textured model: {asset.virtual_path}")
        self._start_texture_resolve_worker(asset)

    def _finish_texture_preview_queue(self, completed_asset_id: str) -> None:
        queued = self._queued_preview_asset_id
        self._queued_preview_asset_id = None
        current = self.selected_asset()
        if queued and current and current.asset_id == queued and current.asset_id != completed_asset_id:
            self._schedule_auto_preview()

    def _texture_resolve_finished(self, asset_id: str, result: object, texture_asset_id: str, report: str) -> None:
        self._last_texture_resolve_report[asset_id] = report
        if texture_asset_id:
            self._pinned_texture_asset_id = texture_asset_id
            tex = self.assets_by_id.get(texture_asset_id)
            if tex and tex.magic == "BTX0":
                self._update_status(f"Texture resolve selected external texture archive {tex.virtual_path} for future previews/exports.")
        elif "embedded TEX0" in report:
            self._update_status("Texture resolve used embedded NSBMD texture data; no external texture archive was pinned.")
        current = self.assets_by_id.get(asset_id)
        if current:
            self._selected_asset_id = asset_id
        if getattr(result, "output_files", None):
            first = best_preview_path(result.output_files) or result.output_files[0]
            fallback_paths = list(getattr(result, "auxiliary_files", []))
            self._store_texture_resolution_cache(
                asset_id,
                report=report,
                selected_texture_id=texture_asset_id,
                preview_path=first,
                auxiliary_paths=fallback_paths,
            )
            self.preview.load_glb(first, fallback_textures=fallback_paths)
            self._last_previewed_asset_id = asset_id
            self._preview_fallback_count_by_asset_id[asset_id] = len(fallback_paths)
            self._preview_status_by_asset_id[asset_id] = self._preview_result_text(first, fallback_paths)
            quality = converted_texture_quality(first)
            if quality.confident:
                self._update_status(f"Previewing {first}. {quality.summary()}.")
            elif fallback_paths:
                self._update_status(f"Previewing {first} with {len(fallback_paths)} decoded texture PNG fallback(s).")
            else:
                self._update_status(f"Previewing {first}. No verified texture images were decoded.")
            self._update_preview_details(current)
        switch_to_details = getattr(self, "_texture_preview_switch_to_details", False)
        self._texture_preview_switch_to_details = False
        if switch_to_details:
            self.show_selected_details()
            self.info_tabs.setCurrentWidget(self.details)

        self._finish_texture_preview_queue(asset_id)

    def _texture_resolve_failed(self, asset_id: str, message: str) -> None:
        self._last_texture_resolve_report[asset_id] = "Texture resolve report\n" + message
        current = self.selected_asset()
        if current and current.asset_id == asset_id:
            self._update_status("Textured preview failed; falling back to geometry-only preview.")
            self._start_geometry_preview(current, manual=False)
        else:
            self.preview.show_message(f"Textured preview failed:\n{message}")
            self._update_status(f"Textured preview failed: {message}")
        self._texture_preview_switch_to_details = False
        self._finish_texture_preview_queue(asset_id)
        self.show_selected_details()

    def pin_selected_texture(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BTX0/NSBTX texture asset first.")
            return
        if asset.magic != "BTX0":
            QMessageBox.information(self, "Not a texture", "Select a BTX0/NSBTX texture row first. Try filtering for BTX0 or a/0/1/4.")
            return
        self._pinned_texture_asset_id = asset.asset_id
        self.show_selected_details()
        self._update_status(f"Pinned texture archive for previews/exports: {asset.virtual_path}")

    def clear_pinned_texture(self) -> None:
        self._pinned_texture_asset_id = None
        self.show_selected_details()
        self._update_status("Cleared pinned texture archive.")

    def _pinned_texture_asset(self) -> Asset | None:
        if not self._pinned_texture_asset_id:
            return None
        for asset in self.assets:
            if asset.asset_id == self._pinned_texture_asset_id and asset.magic == "BTX0":
                return asset
        return None

    def extract_selected_texture_images(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BTX0/NSBTX texture asset first.")
            return
        if asset.magic != "BTX0":
            QMessageBox.information(self, "Not a texture", "Select a BTX0/NSBTX texture row first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose texture image export folder")
        if not out_dir:
            return
        base = Path(out_dir) / f"dsm_textures_{asset.asset_id}"
        self._update_status(f"Extracting texture images from {asset.virtual_path}...")

        # v8 first tries RAE's own NSBTX decoder. It is faster and does not need
        # apicula for common indexed/direct DS texture formats.
        try:
            decoded = decode_btx_images(asset.data, max_images=256, mode="all-palettes")
            written = export_readable_asset(asset, out_dir) if decoded else []
        except Exception:
            decoded = []
            written = []
        if written:
            QMessageBox.information(self, "Textures extracted", f"RAE decoded and wrote {len(written)} PNG file(s) to:\n{Path(out_dir)}")
            self._update_status(f"RAE decoded {len(written)} texture PNG(s) to {out_dir}")
            return

        # Fallback: apicula may still be useful for weird model/texture cases.
        if not apicula_available():
            QMessageBox.warning(self, "Texture extraction incomplete", "RAE could not decode this BTX0 directly, and apicula was not found for fallback extraction.\n\n" + apicula_help_text())
            self._update_status("Texture extraction failed: no direct decode and no apicula fallback.")
            return
        result = convert_texture_with_apicula(asset, base)
        if result.ok:
            images = texture_outputs(base)
            QMessageBox.information(self, "Textures extracted", f"apicula wrote {len(images)} image/file(s) to:\n{base}")
            self._update_status(f"apicula extracted {len(images)} texture image/file(s) to {base}")
        else:
            QMessageBox.warning(self, "Texture extraction failed", result.message)
            self._update_status("Texture extraction failed.")


