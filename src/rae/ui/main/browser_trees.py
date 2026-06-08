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

class BrowserTreesMixin:
    def _unmapped_type_bucket(self, asset: Asset) -> str:
        if asset.magic == "BMD0":
            return "models"
        if asset.magic == "BTX0":
            return "textures"
        if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "PNG", "NFTR"}:
            return "sprites / images / fonts"
        if asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
            return "audio / music / SFX"
        if asset.magic in {"BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}:
            return "model animations"
        return asset.mapping_category or asset.kind or "other"

    def _rom_folder_segments_for_asset(self, asset: Asset) -> tuple[str, ...]:
        folder = (asset.folder_key or "").replace("\\", "/").strip("/")
        if folder:
            return tuple(part for part in folder.split("/") if part)
        vpath = (asset.virtual_path or "").replace("\\", "/").strip("/")
        if not vpath:
            return ()
        parts = [part for part in vpath.split("/") if part]
        if len(parts) <= 1:
            return tuple(parts)
        return tuple(parts[:-1])

    def _build_raw_folder_child_map(self, assets: list[Asset]) -> dict[tuple[str, ...], set[str]]:
        children: dict[tuple[str, ...], set[str]] = {}
        for asset in assets:
            parts = self._rom_folder_segments_for_asset(asset)
            for index, segment in enumerate(parts):
                children.setdefault(parts[:index], set()).add(segment)
        return children

    def _collapse_raw_folder_parts(
        self,
        folder_parts: tuple[str, ...],
        child_map: dict[tuple[str, ...], set[str]],
    ) -> tuple[str, ...]:
        """Merge single-child ROM folder chains like a/0/0/8 into one tree node."""
        if len(folder_parts) <= 1:
            return folder_parts
        collapsed: list[str] = []
        index = 0
        while index < len(folder_parts):
            end = index
            while end + 1 < len(folder_parts):
                prefix = folder_parts[: end + 1]
                kids = child_map.get(prefix, set())
                if len(kids) == 1 and folder_parts[end + 1] in kids:
                    end += 1
                else:
                    break
            collapsed.append("/".join(folder_parts[index : end + 1]))
            index = end + 1
        return tuple(collapsed)

    def _initial_raw_tree_folder_parts(self, assets: list[Asset] | None = None) -> tuple[str, ...]:
        assets = assets if assets is not None else self.visible_assets
        if not assets:
            return ()
        child_map = self._build_raw_folder_child_map(assets)
        counts: Counter[tuple[str, ...]] = Counter()
        for asset in assets:
            folder_parts = self._collapse_raw_folder_parts(self._rom_folder_segments_for_asset(asset), child_map)
            if folder_parts:
                counts[folder_parts] += 1
        if counts:
            return counts.most_common(1)[0][0]
        return ()

    def _find_raw_tree_folder_item(self, parts: tuple[str, ...]) -> QTreeWidgetItem | None:
        if not parts or not hasattr(self, "raw_tree"):
            return None
        if len(parts) == 1:
            root = self.raw_tree.invisibleRootItem()
            for i in range(root.childCount()):
                child = root.child(i)
                data = child.data(0, Qt.UserRole)
                if isinstance(data, dict) and tuple(data.get("folder", ())) == parts:
                    return child
            return None
        parent = self._find_raw_tree_folder_item(parts[:-1])
        if parent is None:
            return None
        parent_parts = parts[:-1]
        if parent_parts not in self._raw_tree_loaded_groups:
            self._populate_folder_children(parent, parent_parts, True)
            self._raw_tree_loaded_groups.add(parent_parts)
        if not parent.isExpanded():
            parent.setExpanded(True)
        for i in range(parent.childCount()):
            child = parent.child(i)
            data = child.data(0, Qt.UserRole)
            if isinstance(data, dict) and tuple(data.get("folder", ())) == parts:
                return child
        return None

    def _focus_browser_on_rom_folders(self) -> None:
        if not hasattr(self, "browser_tabs") or not self.visible_assets:
            return
        raw_index = self.browser_tabs.indexOf(self.raw_tree)
        if raw_index < 0:
            return
        self.browser_tabs.setCurrentIndex(raw_index)
        self._populate_raw_tree(self.visible_assets)
        self._browser_tab_versions[id(self.raw_tree)] = self._visible_assets_version

        parts = self._initial_raw_tree_folder_parts(self.visible_assets)
        if not parts:
            return
        item = self._find_raw_tree_folder_item(parts)
        if item is None:
            return
        self._expand_raw_folder_chain(item)
        self.raw_tree.setCurrentItem(item)
        self.raw_tree.scrollToItem(item)
        self.on_selection_changed()

    def _expand_raw_folder_chain(self, item: QTreeWidgetItem) -> None:
        item.setExpanded(True)
        while item.childCount() == 1:
            child = item.child(0)
            data = child.data(0, Qt.UserRole)
            if isinstance(data, dict) and data.get("placeholder"):
                parent_data = item.data(0, Qt.UserRole)
                if isinstance(parent_data, dict) and "folder" in parent_data:
                    parts = tuple(parent_data["folder"])
                    if parts not in self._raw_tree_loaded_groups:
                        self._populate_folder_children(item, parts, True)
                        self._raw_tree_loaded_groups.add(parts)
                break
            if isinstance(data, dict) and "folder" in data:
                parts = tuple(data["folder"])
                if parts not in self._raw_tree_loaded_groups:
                    self._populate_folder_children(child, parts, True)
                    self._raw_tree_loaded_groups.add(parts)
                item = child
                item.setExpanded(True)
                continue
            break

    def _tree_parts_for_asset(self, asset: Asset) -> tuple[str, ...]:
        """Return compact tree folders for an asset.

        Pokémon DS paths like a/0/3/9 used to become four separate folders. For
        browsing, that is just friction, so RAE keeps mapped category/label nodes
        and collapses the real ROM folder into one readable path segment.
        """
        folder = (asset.folder_key or "/").replace("\\", "/").strip("/")
        rom_folder = "/".join(self._rom_folder_segments_for_asset(asset)) or folder or "(rom root)"
        if (asset.mapping_confidence or "") == "format-signature" or not asset.mapping_label or asset.mapping_label == "Detected by signature":
            return ("Unmapped", self._unmapped_type_bucket(asset), rom_folder)
        category = asset.mapping_category or "unknown"
        label = asset.mapping_label or "Detected by signature"
        return (category, label, rom_folder)

    def _raw_tree_parts_for_asset(
        self,
        asset: Asset,
        child_map: dict[tuple[str, ...], set[str]] | None = None,
    ) -> tuple[str, ...]:
        folder_parts = self._rom_folder_segments_for_asset(asset)
        if folder_parts:
            if child_map is None:
                child_map = self._build_raw_folder_child_map(self.assets or [asset])
            folder_parts = self._collapse_raw_folder_parts(folder_parts, child_map)
        magic = asset.magic or asset.kind or "unknown"
        return (*folder_parts, magic) if folder_parts else (magic,)

    def _populate_mapped_tree(self, assets: list[Asset]) -> None:
        """Build compact mapped-tree folder nodes and add asset leaves lazily."""
        if not hasattr(self, "tree"):
            return
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self._tree_group_rows = {}
            self._tree_loaded_groups = set()
            folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}

            def get_folder(parts: tuple[str, ...]) -> QTreeWidgetItem:
                if parts in folder_nodes:
                    return folder_nodes[parts]
                parent = self.tree.invisibleRootItem() if len(parts) == 1 else get_folder(parts[:-1])
                item = QTreeWidgetItem([parts[-1], "", "", ""])
                item.setData(0, Qt.UserRole, {"folder": parts})
                parent.addChild(item)
                folder_nodes[parts] = item
                return item

            parts_cache = self._mapped_tree_parts_by_id
            for row, asset in enumerate(assets):
                parts = parts_cache.get(asset.asset_id) or self._tree_parts_for_asset(asset)
                self._tree_group_rows.setdefault(parts, []).append(row)
                get_folder(parts)

            for parts, rows in self._tree_group_rows.items():
                item = folder_nodes.get(parts)
                if item is not None:
                    self._apply_tree_folder_labels(item, parts, rows, raw=False)
                if item is not None and item.childCount() == 0:
                    count = len(rows)
                    placeholder = QTreeWidgetItem([f"Open to load {count} asset(s)", "", "", ""])
                    placeholder.setData(0, Qt.UserRole, {"placeholder": True})
                    item.addChild(placeholder)

            root = self.tree.invisibleRootItem()
            expand_limit = min(root.childCount(), 32)
            for i in range(expand_limit):
                root.child(i).setExpanded(True)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)

    def _populate_raw_tree(self, assets: list[Asset]) -> None:
        if not hasattr(self, "raw_tree"):
            return
        self.raw_tree.setUpdatesEnabled(False)
        self.raw_tree.blockSignals(True)
        try:
            self.raw_tree.clear()
            self._raw_tree_group_rows = {}
            self._raw_tree_loaded_groups = set()
            folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}

            def get_folder(parts: tuple[str, ...]) -> QTreeWidgetItem:
                if parts in folder_nodes:
                    return folder_nodes[parts]
                parent = self.raw_tree.invisibleRootItem() if len(parts) == 1 else get_folder(parts[:-1])
                item = QTreeWidgetItem([parts[-1], "", "", ""])
                item.setData(0, Qt.UserRole, {"folder": parts, "raw": True})
                parent.addChild(item)
                folder_nodes[parts] = item
                return item

            child_map = self._build_raw_folder_child_map(assets)
            parts_cache = self._raw_tree_parts_by_id
            for row, asset in enumerate(assets):
                parts = parts_cache.get(asset.asset_id) or self._raw_tree_parts_for_asset(asset, child_map)
                self._raw_tree_group_rows.setdefault(parts, []).append(row)
                get_folder(parts)

            for parts, rows in self._raw_tree_group_rows.items():
                item = folder_nodes.get(parts)
                if item is not None:
                    self._apply_tree_folder_labels(item, parts, rows, raw=True)
                if item is not None and item.childCount() == 0:
                    count = len(rows)
                    placeholder = QTreeWidgetItem([f"Open to load {count} asset(s)", "", "", ""])
                    placeholder.setData(0, Qt.UserRole, {"placeholder": True})
                    item.addChild(placeholder)

            initial_parts = self._initial_raw_tree_folder_parts(assets)
            if initial_parts:
                initial_item = folder_nodes.get(initial_parts)
                if initial_item is not None:
                    self._expand_raw_folder_chain(initial_item)
        finally:
            self.raw_tree.blockSignals(False)
            self.raw_tree.setUpdatesEnabled(True)

    def _on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, dict) or "folder" not in data:
            return
        parts = tuple(data["folder"])
        is_raw = bool(data.get("raw"))
        loaded_groups = self._raw_tree_loaded_groups if is_raw else self._tree_loaded_groups
        if parts in loaded_groups:
            return
        self._populate_folder_children(item, parts, is_raw)
        loaded_groups.add(parts)

    def _populate_folder_children(self, item: QTreeWidgetItem, parts: tuple[str, ...], is_raw: bool) -> None:
        tree = self.raw_tree if is_raw and hasattr(self, "raw_tree") else self.tree
        group_rows = self._raw_tree_group_rows if is_raw else self._tree_group_rows
        rows = group_rows.get(parts, [])
        if not rows:
            return
        tree.blockSignals(True)
        item.takeChildren()
        for source_row in rows:
            if not (0 <= source_row < len(self.visible_assets)):
                continue
            asset = self.visible_assets[source_row]
            leaf = QTreeWidgetItem(self._browser_row_values(asset))
            leaf.setData(0, Qt.UserRole, source_row)
            item.addChild(leaf)
        item.setData(0, Qt.UserRole, {"folder": parts, "raw": is_raw})
        tree.blockSignals(False)
        tree_name = "Raw Folders" if is_raw else "Mapped Tree"
        self._update_status(f"Loaded {len(rows)} asset(s) in {tree_name}: {' / '.join(parts)}.")

