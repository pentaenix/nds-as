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

class DetailsMixin:
    def show_selected_details(self) -> None:
        asset = self.selected_asset()
        if not asset:
            self.details.clear()
            return
        details = [
            f"Name: {self._asset_display_name(asset)}",
            f"File: {self._asset_file_label(asset)}",
            f"Path: {asset.virtual_path}",
            f"Kind: {asset.kind}",
            f"Magic: {asset.magic}",
            f"Mapping category: {asset.mapping_category or 'unknown'}",
            f"Mapping label: {asset.mapping_label or 'none'}",
            f"Mapping confidence: {asset.mapping_confidence or 'none'}",
            f"Decoded size: {human_size(asset.size)}",
            f"Original size: {human_size(asset.original_size)}",
            f"Compressed: {'yes' if asset.compressed else 'no'}",
            f"Carved from unknown container: {'yes' if asset.carved else 'no'}",
            f"ROM file id: {asset.rom_file_id if asset.rom_file_id is not None else 'unknown'}",
            f"ROM offset: 0x{asset.rom_offset:X}" if asset.rom_offset is not None else "ROM offset: unknown",
        ]
        if asset.carved_offset is not None:
            details.append(f"Carved offset: 0x{asset.carved_offset:X}")
        if asset.container_chain:
            details.append("Containers:")
            details.extend(f"  - {c}" for c in asset.container_chain)

        hunt_report = self._last_texture_resolve_report.get(asset.asset_id)
        if hunt_report:
            details.append("")
            details.append(hunt_report)
        pinned = self._pinned_texture_asset()
        if pinned:
            details.append("")
            details.append(f"Pinned BTX0 texture: {pinned.virtual_path}")

        if asset.magic == "BMD0":
            details.append("")
            details.append("Model preview/export uses apicula.")
            details.append("Use Set Textures to match exact NSBMD material names to NSBTX texture/palette dictionaries while keeping this model selected.")
            details.append("Set Textures writes decoded texture PNGs for the preview/export bundle. Use Selected BTX0 is still available for a manual override.")
            details.append("Progress for model conversion and conversion and texture decoding progress appears in Terminal.")
            if not apicula_available():
                details.append("")
                details.append("Tip: build apicula in tools/apicula/target/release/apicula or set DSAS_APICULA to its path.")
        elif asset.magic == "BTX0":
            parent = self._texture_slot_parent_asset(asset) if getattr(asset, "is_texture_slot", False) else None
            archive = parent or asset
            entries = self._btx0_texture_entries(archive)
            names = [name for name, *_rest in entries] or sorted(self._asset_names(archive))
            details.append("")
            if getattr(asset, "is_texture_slot", False) and asset.texture_slot:
                details.append(f"Texture slot: {asset.texture_slot}")
                if parent is not None:
                    details.append(f"Parent archive: {parent.virtual_path}")
                self._selected_btx0_texture_name = asset.texture_slot
            self._sync_btx0_texture_combo(archive)
            preview_name = (
                asset.texture_slot
                if getattr(asset, "is_texture_slot", False) and asset.texture_slot
                else self._selected_btx0_texture_name or self._preview_btx0_texture_name(archive)
            )
            if not getattr(asset, "is_texture_slot", False):
                details.append(f"Texture dictionary entries: {len(entries) or len(names)}")
            if preview_name:
                entry = next((row for row in entries if row[0] == preview_name), None)
                details.append(f"Preview entry: {preview_name}")
                if entry:
                    _name, fmt, width, height = entry
                    details.append(f"  Format: {fmt}  Size: {width}x{height}")
            if not getattr(asset, "is_texture_slot", False) and len(entries) > 1:
                details.append("  Each dictionary slot also appears as its own row under Texture slots in the browser.")
                details.append("  Use the Texture entry dropdown (preview panel) to switch slots on the archive row.")
            details.append("Texture preview/decode runs on a worker thread when you select a row.")
            if not getattr(asset, "is_texture_slot", False):
                details.append("Tip: click Use Selected BTX0 to pin this texture archive for the next BMD0 preview, or Extract Texture PNGs / Export Readable to save PNGs.")
        elif asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR"}:
            details.append("")
            if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
                if asset.size > 8 * 1024 * 1024:
                    previews = []
                    details.append("RAE direct preview images: skipped in Details for large asset; use Preview to decode in a worker.")
                else:
                    try:
                        previews = decode_nitro2d_preview(asset.data, asset.magic)
                    except Exception:
                        previews = []
                    details.append(f"RAE direct preview images: {len(previews)}")
                    for img in previews:
                        details.append(f"  - {img.name}: {img.width}x{img.height} ({img.source})")
                details.append("Tip: Export Selected… offers raw export plus readable PNG/contact sheet output. Use Export Selected… to create a combined readable bundle when RAE can pair the files.")
            else:
                details.append("RAE can identify this asset type. Export Selected saves the raw file.")
        elif asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
            details.append("")
            if asset.magic == "SDAT":
                details.append("SDAT archive: Export Readable writes a lossless audio bundle with child SSEQ/SSAR/SBNK/SWAR/STRM files and WAV previews where RAE can decode samples/streams.")
            elif asset.magic == "SWAR":
                details.append("SWAR sample archive: Export Readable extracts child SWAV samples and WAV previews when possible.")
            elif asset.magic in {"SWAV", "STRM"}:
                details.append("Sample/stream audio: Export Readable writes raw original plus WAV when RAE can decode the payload.")
            else:
                details.append("Sequenced/instrument audio: Export Readable writes the original raw file losslessly. Use VGMTrans/Nitro Studio for MIDI/SF2-style rendering.")
        elif asset.magic == "PNG" or asset.data.startswith(b"\x89PNG"):
            details.append("")
            details.append("PNG preview/export supported directly.")
        self.details.setPlainText("\n".join(details))
        self._update_preview_details(asset)

    def _preview_result_text(self, path: Path, fallback_textures: list[Path] | None = None) -> str:
        quality = converted_texture_quality(path)
        fallback_count = len(fallback_textures or [])
        if quality.mesh_faces <= 0:
            return f"Converted file has no visible mesh geometry: {path.name}"
        if quality.confident:
            return f"Preview file: {path.name} — {quality.summary()}."
        if fallback_count:
            return f"Preview file: {path.name} — {quality.summary()}. RAE decoded {fallback_count} texture PNG(s) for preview/export fallback; the GLB itself may still not embed images."
        if quality.weak_material_only:
            return f"Preview file: {path.name} — {quality.summary()}. The converted file does not prove visible texture sampling yet."
        return f"Preview file: {path.name} — mesh faces: {quality.mesh_faces}; no verified texture image detected."

    def _update_preview_details(self, asset: Asset | None = None) -> None:
        if not hasattr(self, "preview_details"):
            return
        asset = asset or self.selected_asset()
        if not asset:
            self.preview_details.setPlainText(
                "Preview status\n"
                "  Select an asset to see preview-specific notes here.\n"
                "  Model texture resolve results and GLB preview status appear for BMD0 models."
            )
            if hasattr(self, "reset_view_button"):
                self.reset_view_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.pin_texture_button.setVisible(False)
            self.clear_pin_button.setVisible(False)
            if hasattr(self, "_update_preview_inspector_visibility"):
                self._update_preview_inspector_visibility(None)
            return

        pinned = self._pinned_texture_asset()
        lines = [
            "Selection",
            f"  Name: {self._asset_display_name(asset)}",
            f"  File: {asset_filename_label(asset.virtual_path)}",
            f"  {asset.magic or asset.kind}: {asset.virtual_path}",
            f"  Mapping: {asset.mapping_label or 'unmapped'}",
        ]
        if asset.magic == "BMD0":
            lines.append("")
            lines.append("Model texture status")
            if hasattr(self, "_model_preview_policy"):
                policy = self._model_preview_policy()
                lines.append(f"  Preview quality: {policy.label} ({policy.summary()})")
            lines.append(f"  Pinned external texture: {pinned.virtual_path if pinned else 'none'}")
            fallback_count = self._preview_fallback_count_by_asset_id.get(asset.asset_id, 0)
            if fallback_count:
                lines.append(f"  RAE decoded preview/export texture PNGs: {fallback_count}")
            report = self._last_texture_resolve_report.get(asset.asset_id, "")
            if "embedded TEX0 decoded" in report:
                lines.append("  Embedded NSBMD texture: decoded and available as RAE fallback PNGs")
            elif "embedded TEX0 texture block found" in report:
                lines.append("  Embedded NSBMD texture: detected; Set Textures can decode/trace it")
            status = self._preview_status_by_asset_id.get(asset.asset_id)
            if status:
                lines.append(f"  {status}")
            lines.append("")
            lines.append("  RAE auto-resolves textures on preview. Open Texture Assigner to match textures to model parts.")
            saved = len(self._texture_assignments.get(asset.asset_id, {}))
            if saved:
                lines.append(f"  Manual texture assignments saved in session: {saved} part(s)")
            if hasattr(self, "_texture_sequence_summary"):
                seq_summary = self._texture_sequence_summary(asset)
                if seq_summary:
                    lines.append(f"  {seq_summary}")
                    lines.append("  Use Animation States and ▶ in the viewport for flipbook playback.")
        elif asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
            lines.append("")
            lines.append("Sprite/tile status")
            lines.append("  NCGR/RGCN = tile pixels; NCLR/RLCN = palette; NCER/RECN = cell layout; NANR/RNAN = animation timing.")
            lines.append("  Multi-entry previews open the Texture Sheet tab to pick individual decoded entries.")
        elif asset.magic == "BTX0":
            lines.append("")
            lines.append("Texture archive status")
            lines.append("  Preview decodes NSBTX entries into a contact sheet when multiple textures are present.")
            lines.append("  Use the Texture Sheet tab to inspect one decoded texture at a time.")
        elif asset.magic in {"SDAT", "SWAR", "SWAV", "STRM", "SSEQ", "SSAR", "SBNK"}:
            lines.append("")
            lines.append("Audio status")
            lines.append("  Export Selected writes the original data and WAV previews where RAE can decode samples/streams.")
        else:
            lines.append("")
            lines.append("Use Preview or Export Selected for the available decoder/export options.")

        self.preview_details.setPlainText("\n".join(lines))
        pinned_active = pinned is not None
        self.export_button.setEnabled(True)
        self.export_button.setVisible(True)
        preview_active = getattr(self.preview, "_last_path", None) is not None
        if hasattr(self, "reset_view_button"):
            self.reset_view_button.setEnabled(preview_active and asset.magic == "BMD0")
            self.reset_view_button.setVisible(True)
        self.pin_texture_button.setEnabled(asset.magic == "BTX0")
        self.pin_texture_button.setVisible(asset.magic == "BTX0")
        self.clear_pin_button.setEnabled(pinned_active)
        self.clear_pin_button.setVisible(pinned_active)
        if hasattr(self, "_refresh_texture_assigner"):
            self._refresh_texture_assigner(asset)
        if hasattr(self, "_update_preview_inspector_visibility"):
            self._update_preview_inspector_visibility(asset)


