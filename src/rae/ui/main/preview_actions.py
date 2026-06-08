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

class PreviewActionsMixin:
    def _preview_btx0_texture(self, asset: Asset, texture_name: str) -> None:
        self._preview_decodable_images(asset, f"BTX0 texture {texture_name}", texture_name=texture_name)

    def preview_selected_asset(self, *, manual: bool = True, force: bool = False) -> None:
        asset = self.selected_asset()
        if not asset:
            if manual:
                QMessageBox.information(self, "No selection", "Select an asset first.")
            return
        if asset.magic == "BMD0":
            self.convert_preview_selected(manual=manual, force=force)
            return
        if asset.magic == "BTX0":
            texture_name = self._selected_btx0_texture_name or self._preview_btx0_texture_name(asset)
            if texture_name:
                self._preview_btx0_texture(asset, texture_name)
                return
            self.preview.show_message(
                f"No named texture entries found in this NSBTX archive yet.\n\n"
                f"{asset.virtual_path}\n\n"
                "Use Export Selected → readable for a full decode attempt."
            )
            self._update_status(f"BTX0 archive has no dictionary names: {asset.virtual_path}")
            return
        if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
            self._preview_decodable_images(asset, asset.kind)
            return
        if asset.magic in {"SDAT", "SWAR", "SWAV", "STRM", "SSEQ", "SSAR", "SBNK"}:
            self.preview.show_message(
                f"Audio asset selected. Use Export Selected… to write raw files and WAV previews where possible.\n\n{asset.virtual_path}"
            )
            self._update_status(f"Audio selected: {asset.virtual_path}")
            return
        if asset.magic == "PNG" or asset.data.startswith(b"\x89PNG"):
            out = self.preview_temp / f"{asset.asset_id}.png"
            out.write_bytes(asset.data)
            self.preview.show_image_path(out, f"PNG image: {asset.virtual_path}")
            self._update_status(f"Previewing PNG image {asset.virtual_path}")
            return
        if manual:
            QMessageBox.information(self, "No visual decoder yet", f"RAE can export this asset raw, but does not have a visual preview for {asset.magic or asset.kind} yet.")
        else:
            self.preview.show_message(f"No visual preview decoder yet for this asset.\n\n{asset.kind} / {asset.magic}\n{asset.virtual_path}")

    def _quick_related_2d_assets(self, asset: Asset, *, limit: int = 16) -> list[Asset]:
        """Cheap same-folder fallback so NCER/NANR can preview from nearby partners.

        Nitro 2D files rarely carry friendly cross-file names. The game usually
        loads a small group by archive/table context, so for browsing we rank
        assets in the same folder/container by numeric file index and choose the
        nearest graphics, palette, cell, screen, and animation partners.
        """
        wanted = {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}
        source_num = self._asset_numeric_tail(asset)
        ranked: list[tuple[int, int, str, Asset]] = []
        for candidate in self.assets:
            if candidate.asset_id == asset.asset_id or candidate.magic not in wanted:
                continue
            same_folder = candidate.folder_key == asset.folder_key
            same_container = bool(asset.container_chain and candidate.container_chain and asset.container_chain[-1:] == candidate.container_chain[-1:])
            same_category = bool(asset.mapping_category and asset.mapping_category == candidate.mapping_category)
            if not (same_folder or same_container or same_category):
                continue
            cand_num = self._asset_numeric_tail(candidate)
            distance = abs(cand_num - source_num) if cand_num is not None and source_num is not None else 9999
            context = 0 if same_folder else (1 if same_container else 2)
            ranked.append((context, distance, candidate.virtual_path, candidate))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))

        # Keep a balanced set instead of returning the first N, which often means
        # many palettes but no tiles/cells.
        out: list[Asset] = []
        seen: set[str] = set()
        per_magic: dict[str, int] = {}
        for _context, _distance, _path, candidate in ranked:
            count = per_magic.get(candidate.magic, 0)
            if count >= 4:
                continue
            out.append(candidate)
            seen.add(candidate.asset_id)
            per_magic[candidate.magic] = count + 1
            if len(out) >= limit:
                break
        # Ensure at least one of each nearby core type where possible.
        for magic in ("RGCN", "RLCN", "RCSN", "RECN", "RNAN"):
            if any(a.magic == magic for a in out):
                continue
            for _context, _distance, _path, candidate in ranked:
                if candidate.magic == magic and candidate.asset_id not in seen:
                    out.append(candidate)
                    seen.add(candidate.asset_id)
                    break
        return out[:limit]

    def _asset_numeric_tail(self, asset: Asset) -> int | None:
        nums = re.findall(r"\d+", asset.virtual_path)
        return int(nums[-1]) if nums else None

    def _preview_decodable_images(self, asset: Asset, label: str, *, texture_name: str | None = None) -> None:
        self._image_preview_request_id += 1
        request_id = self._image_preview_request_id
        related: list[Asset] = []
        if texture_name:
            out = self.preview_temp / f"btx_{asset.asset_id}" / f"{texture_name}.png"
            self.preview.show_message(f"Decoding texture '{texture_name}' from {asset.virtual_path}...")
            self._update_status(f"Decoding BTX0 texture entry '{texture_name}' from {asset.virtual_path}")
        else:
            out = self.preview_temp / f"preview_{asset.asset_id}.png"
            if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
                related = self._quick_related_2d_assets(asset, limit=16)
            related_note = f"\nUsing {len(related)} related asset(s)." if related else ""
            self.preview.show_message(f"Decoding preview off the UI thread...\n{asset.virtual_path}{related_note}")
            self._update_status(f"Starting preview decode for {asset.virtual_path}; related assets: {len(related)}")
        self.image_preview_worker = ImagePreviewWorker(
            asset,
            out,
            label,
            request_id=request_id,
            related_assets=related,
            texture_name=texture_name,
        )
        self.image_preview_worker.progress.connect(self._update_status)
        self.image_preview_worker.finished_ok.connect(self._image_preview_finished)
        self.image_preview_worker.failed.connect(self._image_preview_failed)
        self.image_preview_worker.start()

    def _image_preview_still_current(self, request_id: int, asset_id: str) -> bool:
        if request_id != self._image_preview_request_id:
            return False
        current = self.selected_asset()
        if not current or current.asset_id != asset_id:
            return False
        if current.magic == "BTX0":
            expected = self._selected_btx0_texture_name or self._preview_btx0_texture_name(current)
            worker = self.image_preview_worker
            if worker is not None and worker.texture_name != expected:
                return False
        return True

    def _image_preview_finished(self, request_id: int, asset_id: str, path: str, caption: str) -> None:
        if not self._image_preview_still_current(request_id, asset_id):
            self._update_status("Preview decode finished for a previously selected row.")
            return
        self.preview.show_image_path(Path(path), caption)
        self._update_status(f"Preview decoded: {Path(path).name}")

    def _image_preview_failed(self, request_id: int, asset_id: str, message: str) -> None:
        if not self._image_preview_still_current(request_id, asset_id):
            self._update_status(f"Preview decode failed for a previously selected row: {message}")
            return
        current = self.selected_asset()
        if current and current.magic == "BTX0" and self._selected_btx0_texture_name:
            self.preview.show_message(
                f"Could not decode texture '{self._selected_btx0_texture_name}' from:\n{current.virtual_path}\n\n"
                f"{message}\n\n"
                "Try another texture entry from the expanded BTX0 row, or Export Readable for diagnostics."
            )
        else:
            self.preview.show_message(
                f"RAE found this asset, but could not decode a preview image yet.\n\n"
                f"{current.virtual_path if current else asset_id}\n\n"
                f"{message}\n\n"
                "Use Export Selected for raw data, readable PNG/WAV outputs, or a model/audio bundle when available."
            )
        self._update_status(f"Preview decode failed: {message}")

