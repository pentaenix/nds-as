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

class FiltersMixin:
    def _schedule_apply_filter(self) -> None:
        if hasattr(self, "_filter_timer"):
            self._filter_timer.start()
        else:
            self.apply_filter()

    def _on_type_filter_toggled(self, _checked: bool = False) -> None:
        self._persist_type_filter_preferences()
        self._update_show_types_button_label()
        self._schedule_apply_filter()

    def _type_filter_label(self, magic: str) -> str:
        return TYPE_LABELS.get(magic, magic or "unknown")

    def _ordered_type_magics(self, counts: Counter) -> list[str]:
        known = [magic for magic in TYPE_FILTER_ORDER if counts.get(magic)]
        extra = sorted(m for m in counts if m not in TYPE_FILTER_ORDER)
        return known + extra

    def _type_filter_default_checked(self, magic: str) -> bool:
        if magic in self._type_filter_preferences:
            return self._type_filter_preferences[magic]
        return magic in DEFAULT_TYPE_FILTER_ON

    def _persist_type_filter_preferences(self) -> None:
        for magic, checkbox in self._type_filter_checkboxes.items():
            self._type_filter_preferences[magic] = checkbox.isChecked()

    def _add_type_filter_checkbox(self, magic: str, count: int) -> None:
        label = self._type_filter_label(magic)
        checkbox = QCheckBox(f"{label} ({count:,})")
        checkbox.setChecked(self._type_filter_default_checked(magic))
        checkbox.setToolTip(f"{magic} — {count:,} asset(s) in this ROM")
        checkbox.stateChanged.connect(self._on_type_filter_toggled)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 2, 12, 2)
        row_layout.addWidget(checkbox)
        action = QWidgetAction(self._show_types_menu)
        action.setDefaultWidget(row)
        self._show_types_menu.addAction(action)
        self._type_filter_checkboxes[magic] = checkbox

    def _set_all_type_filters(self, checked: bool) -> None:
        for checkbox in self._type_filter_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self._persist_type_filter_preferences()
        self._update_show_types_button_label()
        self._schedule_apply_filter()

    def _rebuild_show_types_menu(self) -> None:
        if not hasattr(self, "_show_types_menu"):
            return
        self._persist_type_filter_preferences()
        self._show_types_menu.clear()
        self._type_filter_checkboxes = {}

        counts = Counter(asset.magic for asset in self.assets if asset.magic)
        if not counts:
            placeholder = QAction("Load a ROM to see asset types", self)
            placeholder.setEnabled(False)
            self._show_types_menu.addAction(placeholder)
            self._update_show_types_button_label()
            return

        select_all = QAction("Select all", self)
        select_all.triggered.connect(lambda *_: self._set_all_type_filters(True))
        clear_all = QAction("Clear all", self)
        clear_all.triggered.connect(lambda *_: self._set_all_type_filters(False))
        self._show_types_menu.addAction(select_all)
        self._show_types_menu.addAction(clear_all)
        self._show_types_menu.addSeparator()

        placed: set[str] = set()
        for index, (group_label, magics) in enumerate(TYPE_FILTER_GROUPS):
            present = [magic for magic in magics if counts.get(magic)]
            if not present:
                continue
            header = QAction(group_label, self)
            header.setEnabled(False)
            self._show_types_menu.addAction(header)
            for magic in present:
                self._add_type_filter_checkbox(magic, counts[magic])
                placed.add(magic)
            has_later = any(
                counts.get(magic)
                for later_label, later_magics in TYPE_FILTER_GROUPS[index + 1 :]
                for magic in later_magics
            ) or any(magic for magic in self._ordered_type_magics(counts) if magic not in placed)
            if has_later:
                self._show_types_menu.addSeparator()

        remaining = [magic for magic in self._ordered_type_magics(counts) if magic not in placed]
        if remaining:
            header = QAction("Other", self)
            header.setEnabled(False)
            self._show_types_menu.addAction(header)
            for magic in remaining:
                self._add_type_filter_checkbox(magic, counts[magic])

        self._update_show_types_button_label()

    def _update_show_types_button_label(self) -> None:
        if not hasattr(self, "show_types_button"):
            return
        if not self._type_filter_checkboxes:
            self.show_types_button.setText("Show types ▾")
            return
        labels = [
            self._type_filter_label(magic)
            for magic, checkbox in self._type_filter_checkboxes.items()
            if checkbox.isChecked()
        ]
        if not labels:
            self.show_types_button.setText("Show: all types ▾")
        elif len(labels) <= 2:
            self.show_types_button.setText("Show: " + ", ".join(labels) + " ▾")
        else:
            self.show_types_button.setText(f"Show: {len(labels)} types ▾")

    def _selected_mapping_filter(self) -> str:
        if not hasattr(self, "mapping_box"):
            return ""
        return str(self.mapping_box.currentData() or "")

    def _enabled_type_filters(self) -> list[str]:
        enabled = [magic for magic, checkbox in self._type_filter_checkboxes.items() if checkbox.isChecked()]
        return enabled

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

    def _rebuild_asset_filter_indexes(self) -> None:
        self._asset_search_text = {}
        self._mapped_tree_parts_by_id = {}
        self._raw_tree_parts_by_id = {}
        child_map = self._build_raw_folder_child_map(self.assets)
        for asset in self.assets:
            self._asset_search_text[asset.asset_id] = asset_search_text(asset)
            self._mapped_tree_parts_by_id[asset.asset_id] = self._tree_parts_for_asset(asset)
            self._raw_tree_parts_by_id[asset.asset_id] = self._raw_tree_parts_for_asset(asset, child_map)

    def _compute_filtered_assets(self) -> list[Asset]:
        assets = self.assets
        enabled_types = self._enabled_type_filters()
        if enabled_types:
            allowed = frozenset(enabled_types)
            assets = [asset for asset in assets if asset.magic in allowed]
        mapping_query = self._selected_mapping_filter()
        if mapping_query:
            assets = filter_assets_indexed(assets, mapping_query, self._asset_search_text)
        text_query = self.filter_box.text().strip()
        if text_query:
            assets = filter_assets_indexed(assets, text_query, self._asset_search_text)
        return list(assets)

    def _populate_current_browser_tab(self, *, force: bool = False) -> None:
        if not hasattr(self, "browser_tabs"):
            return
        widget = self.browser_tabs.currentWidget()
        tab_id = id(widget)
        if not force and self._browser_tab_versions.get(tab_id) == self._visible_assets_version:
            return
        if widget is self.table:
            self._populate_table(self.visible_assets)
        elif widget is self.tree:
            self._populate_mapped_tree(self.visible_assets)
        elif widget is self.raw_tree:
            self._populate_raw_tree(self.visible_assets)
        self._browser_tab_versions[tab_id] = self._visible_assets_version

    def apply_filter(self) -> None:
        if not self.assets:
            self.visible_assets = []
            self._visible_assets_version += 1
            self._populate_current_browser_tab(force=True)
            self._update_pagination_bar()
            return

        self._filter_generation += 1
        generation = self._filter_generation
        enabled_types = self._enabled_type_filters()
        mapping_query = self._selected_mapping_filter()
        text_query = self.filter_box.text().strip()

        if len(self.assets) < 6000:
            self._apply_filter_result(generation, self._compute_filtered_assets())
            return

        if self._filter_worker is not None and self._filter_worker.isRunning():
            self._update_status("Updating filter...")
        else:
            self._update_status(f"Filtering {len(self.assets):,} assets...")
        self._filter_worker = FilterWorker(
            generation,
            self.assets,
            enabled_types=enabled_types,
            mapping_query=mapping_query,
            text_query=text_query,
            search_text_by_id=self._asset_search_text,
        )
        self._filter_worker.finished_ok.connect(self._filter_worker_finished)
        self._filter_worker.failed.connect(self._filter_worker_failed)
        self._filter_worker.start()

    def _apply_filter_result(self, generation: int, assets: list[Asset]) -> None:
        if generation != self._filter_generation:
            return
        self.visible_assets = list(assets)
        self.browser_page = 0
        self._visible_assets_version += 1
        self._populate_current_browser_tab(force=True)
        self._update_pagination_bar()
        if self.assets:
            self._update_status(
                f"Showing {len(self.visible_assets):,} of {len(self.assets):,} asset(s)"
            )

    def _filter_worker_finished(self, generation: int, assets: list[Asset]) -> None:
        self._apply_filter_result(generation, assets)

    def _filter_worker_failed(self, generation: int, message: str) -> None:
        if generation != self._filter_generation:
            return
        self._update_status(f"Filter failed: {message}")

    def apply_selected_preset(self) -> None:
        if not hasattr(self, "preset_box"):
            return
        data = self.preset_box.currentData()
        if not data:
            self.filter_box.blockSignals(True)
            self.filter_box.clear()
            self.filter_box.blockSignals(False)
            self.apply_filter()
            return
        query, description, workflow = data
        self.filter_box.blockSignals(True)
        self.filter_box.setText(query)
        self.filter_box.blockSignals(False)
        msg = description
        if workflow:
            msg += "\n" + "\n".join(f"- {step}" for step in workflow)
        self.details.setPlainText(msg)
        self.info_tabs.setCurrentWidget(self.details)
        self._update_status(f"Applied recipe: {self.preset_box.currentText()}")
        self.apply_filter()

    def _populate_search_presets(self) -> None:
        if not hasattr(self, "preset_box"):
            return
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItem("Recipes…", "")
        mapping = self.current_mapping
        if mapping and getattr(mapping, "search_presets", None):
            for preset in mapping.search_presets:
                if preset.query:
                    self.preset_box.addItem(preset.label or preset.id, (preset.query, preset.description, list(preset.workflow)))
        else:
            self.preset_box.addItem("Battle move effects", ("cat:move-effects|cat:move-animations|path:wazaeffect|path:a/0/2/2|path:a/0/2/3|path:a/0/2/4|path:a/0/2/5|path:a/0/2/9|path:a/0/6/6", "Shows likely move effect graphics, palettes, cells, animations, and scripts.", []))
        self.preset_box.blockSignals(False)

