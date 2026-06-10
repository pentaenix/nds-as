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
from PySide6.QtGui import QAction, QBrush, QGuiApplication
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
    QScrollArea,
    QSplitter,
    QStackedWidget,
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
from ...mapping import choose_mapping, load_mappings, mapping_summary
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

class ShellMixin:
    def setup_shell(self) -> None:
        """Initialize window chrome and shared state. Not named __init__ so Qt's base init is not hijacked."""
        self.setWindowTitle("RAE — Retro Asset Extractor")
        self.resize(1280, 760)

        self.rom_path: str | None = None
        self.assets: list[Asset] = []
        self.visible_assets: list[Asset] = []
        self.worker: ScanWorker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.image_preview_worker: ImagePreviewWorker | None = None
        self._image_preview_request_id = 0
        self.texture_worker: TextureWorker | None = None
        self.texture_resolve_worker: TextureResolveWorker | None = None
        self.texture_warmup_worker: TextureLibraryWarmupWorker | None = None
        self.session_save_worker: SessionSaveWorker | None = None
        self.session_load_worker: SessionLoadWorker | None = None
        self.preview_temp = Path(tempfile.mkdtemp(prefix="dsm_preview_"))
        self.profile_text = ""
        self._queued_preview_asset_id: str | None = None
        self._last_previewed_asset_id: str | None = None
        self._pinned_texture_asset_id: str | None = None
        self._texture_library_store = TextureLibraryStore()
        self._texture_resolution_cache: dict[str, CachedTextureResolution] = {}
        self._texture_preview_switch_to_details = False
        self._init_texture_assigner_state()
        self._init_texture_animation_state()
        self._name_cache: dict[str, set[str]] = {}
        self._display_name_cache: dict[str, str] = {}
        self.current_mapping = None
        self.assets_by_id: dict[str, Asset] = {}
        self._selected_asset_id: str | None = None
        self.page_size = 500
        self.browser_page = 0
        self._type_filter_checkboxes: dict[str, QCheckBox] = {}
        self._type_filter_preferences: dict[str, bool] = {}
        self._tree_group_rows: dict[tuple[str, ...], list[int]] = {}
        self._tree_loaded_groups: set[tuple[str, ...]] = set()
        self.session_path: str | None = None
        self.rom_game_code: str = ""
        self.rom_title: str = ""
        self._last_texture_resolve_report: dict[str, str] = {}
        self._preview_status_by_asset_id: dict[str, str] = {}
        self._preview_fallback_count_by_asset_id: dict[str, int] = {}
        self._raw_tree_group_rows: dict[tuple[str, ...], list[int]] = {}
        self._raw_tree_loaded_groups: set[tuple[str, ...]] = set()
        self._btx0_texture_entries_cache: dict[str, list[tuple[str, int, int, int]]] = {}
        self._selected_btx0_texture_name: str | None = None
        self._init_easyfind_state()

        self._build_ui()
        self._update_status("Open a local .nds ROM to start. Use ./rae run next time to launch this app.")

    def _build_ui(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        open_rom_action = QAction("Open ROM…", self)
        open_rom_action.setShortcut("Ctrl+O")
        open_rom_action.triggered.connect(self.open_rom)
        file_menu.addAction(open_rom_action)

        open_session_action = QAction("Open Session…", self)
        open_session_action.triggered.connect(self.open_session)
        file_menu.addAction(open_session_action)

        save_session_action = QAction("Save Session…", self)
        save_session_action.setShortcut("Ctrl+S")
        save_session_action.triggered.connect(self.save_session)
        file_menu.addAction(save_session_action)

        file_menu.addSeparator()
        export_action = QAction("Export Selected…", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_selected_smart)
        file_menu.addAction(export_action)

        view_menu = menubar.addMenu("&View")
        self.auto_preview_action = QAction("Auto Preview", self)
        self.auto_preview_action.setCheckable(True)
        self.auto_preview_action.setChecked(True)
        self.auto_preview_action.setToolTip("Preview the selected row automatically.")
        self.auto_preview_action.toggled.connect(lambda enabled: self._schedule_auto_preview() if enabled else None)
        view_menu.addAction(self.auto_preview_action)

        self.deep_scan_action = QAction("Deep Scan (next ROM open)", self)
        self.deep_scan_action.setCheckable(True)
        self.deep_scan_action.setChecked(False)
        self.deep_scan_action.setToolTip("Slower fallback scan that carves Nitro files out of unknown containers.")
        view_menu.addAction(self.deep_scan_action)

        show_terminal_action = QAction("Show Terminal", self)
        show_terminal_action.triggered.connect(lambda: self.info_tabs.setCurrentWidget(self.log_box))
        view_menu.addAction(show_terminal_action)

        advanced_menu = menubar.addMenu("&Advanced")
        pin_texture_action = QAction("Pin Selected BTX0", self)
        pin_texture_action.triggered.connect(self.pin_selected_texture)
        advanced_menu.addAction(pin_texture_action)
        clear_texture_action = QAction("Clear Texture Pin", self)
        clear_texture_action.triggered.connect(self.clear_pinned_texture)
        advanced_menu.addAction(clear_texture_action)

        self.export_action = export_action
        self.open_rom_action = open_rom_action

        self.main_toolbar = QToolBar("Main")
        self.main_toolbar.setMovable(False)
        self.addToolBar(self.main_toolbar)
        self.open_rom_toolbar_action = QAction("Open ROM", self)
        self.open_rom_toolbar_action.triggered.connect(self.open_rom)
        self.main_toolbar.addAction(self.open_rom_toolbar_action)
        self.save_session_toolbar_action = QAction("Save Session", self)
        self.save_session_toolbar_action.triggered.connect(self.save_session)
        self.save_session_toolbar_action.setVisible(False)
        self.main_toolbar.addAction(self.save_session_toolbar_action)

        filter_row = QWidget()
        filter_layout = QHBoxLayout(filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)

        self.show_types_button = QPushButton("Show types ▾")
        self.show_types_button.setStyleSheet(DROPDOWN_BUTTON_STYLE)
        self._show_types_menu = QMenu(self)
        self.show_types_button.setMenu(self._show_types_menu)
        self._rebuild_show_types_menu()
        filter_layout.addWidget(self.show_types_button)

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Search — path:a/2/3/3, boat | dock, magic:BMD0")
        self.filter_box.textChanged.connect(self._schedule_apply_filter)
        filter_layout.addWidget(self.filter_box, stretch=1)

        self.mapping_box = QComboBox()
        self.mapping_box.addItem("All mapping", "")
        self.mapping_box.addItem("Mapped only", "mapped")
        self.mapping_box.addItem("Unmapped only", "unmapped")
        self.mapping_box.currentIndexChanged.connect(self.apply_filter)
        filter_layout.addWidget(QLabel("Mapping:"))
        filter_layout.addWidget(self.mapping_box)

        self.preset_box = QComboBox()
        self.preset_box.addItem("Recipes…", "")
        self.preset_box.currentIndexChanged.connect(self.apply_selected_preset)
        filter_layout.addWidget(self.preset_box)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(BROWSER_COLUMNS)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.tree.itemDoubleClicked.connect(lambda *_: self.preview_selected_asset(manual=True, force=True))
        th = self.tree.header()
        th.setSectionResizeMode(0, QHeaderView.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.Stretch)

        self.raw_tree = QTreeWidget()
        self.raw_tree.setColumnCount(4)
        self.raw_tree.setHeaderLabels(BROWSER_COLUMNS)
        self.raw_tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.raw_tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.raw_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.raw_tree.itemDoubleClicked.connect(lambda *_: self.preview_selected_asset(manual=True, force=True))
        raw_th = self.raw_tree.header()
        raw_th.setSectionResizeMode(0, QHeaderView.Stretch)
        raw_th.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        raw_th.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        raw_th.setSectionResizeMode(3, QHeaderView.Stretch)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(FLAT_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.doubleClicked.connect(lambda *_: self.preview_selected_asset(manual=True, force=True))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)

        self.browser_tabs = QTabWidget()
        self.browser_tabs.addTab(self.tree, "Mapped Tree")
        self.browser_tabs.addTab(self.raw_tree, "Raw Folders")
        self.browser_tabs.addTab(self.table, "Flat List")
        self.browser_tabs.currentChanged.connect(self._on_browser_tab_changed)

        self.page_prev_button = QPushButton("◀ Previous")
        self.page_prev_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.page_prev_button.clicked.connect(lambda *_: self._change_browser_page(-1))
        self.page_label = QLabel("Page 1 of 1")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_next_button = QPushButton("Next ▶")
        self.page_next_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.page_next_button.clicked.connect(lambda *_: self._change_browser_page(1))
        self.page_summary = QLabel("")
        self.page_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pagination_row = QWidget()
        pagination_layout = QHBoxLayout(self.pagination_row)
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        pagination_layout.addWidget(self.page_prev_button)
        pagination_layout.addWidget(self.page_label)
        pagination_layout.addWidget(self.page_next_button)
        pagination_layout.addWidget(self.page_summary, stretch=1)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(130)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(130)
        self.log_box.setStyleSheet("font-family: Menlo, Consolas, monospace; font-size: 11px;")

        self.info_tabs = QTabWidget()
        self.info_tabs.addTab(self.details, "Details")
        self.info_tabs.addTab(self.log_box, "Terminal")
        info_corner = QWidget()
        info_corner_layout = QHBoxLayout(info_corner)
        info_corner_layout.setContentsMargins(0, 0, 4, 0)
        info_corner_layout.setSpacing(4)
        self.info_copy_button = self._make_icon_tool_button("Copy panel contents to clipboard", "⎘")
        self.info_clear_button = self._make_icon_tool_button("Clear this panel", "⌫")
        self.info_copy_button.clicked.connect(self._copy_info_panel_contents)
        self.info_clear_button.clicked.connect(self._clear_info_panel_contents)
        info_corner_layout.addWidget(self.info_copy_button)
        info_corner_layout.addWidget(self.info_clear_button)
        self.info_tabs.setCornerWidget(info_corner, Qt.TopRightCorner)

        self.browser_workspace = QWidget()
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(filter_row)
        left_layout.addWidget(self.browser_tabs, stretch=1)
        left_layout.addWidget(self.pagination_row)
        left_layout.addWidget(self.info_tabs)

        self.preview = PreviewWidget()
        self.preview.show_message("")

        self.preview_details = QTextEdit()
        self.preview_details.setReadOnly(True)
        self.preview_details.setMinimumHeight(100)
        self.preview_details.setPlaceholderText("Model preview status and texture resolve details appear here.")

        from ..preview.animation_states import AnimationStatesWidget
        from ..preview.texture_assigner import TextureAssignerWidget

        self.texture_assigner = TextureAssignerWidget()
        self.texture_assigner.assignments_changed.connect(self._on_texture_assignments_changed)
        self.animation_states = AnimationStatesWidget()
        self.animation_states.preview_state_changed.connect(self._on_texture_state_changed)
        self.animation_states.spec_changed.connect(self._on_animation_spec_changed)
        self.texture_states = self.animation_states
        self._animation_states_scroll = QScrollArea()
        self._animation_states_scroll.setWidgetResizable(True)
        self._animation_states_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._animation_states_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._animation_states_scroll.setWidget(self.animation_states)

        self.preview_inspector_tabs = QTabWidget()
        self.preview_inspector_tabs.addTab(self.preview_details, "Preview")
        self.preview_inspector_tabs.addTab(self.texture_assigner, "Texture Assigner")
        self.preview_inspector_tabs.addTab(self._animation_states_scroll, "Animation States")
        self.preview_inspector_tabs.setCurrentIndex(0)
        self.preview_inspector_tabs.setMinimumHeight(140)

        self.reset_view_button = QPushButton("Reset View")
        self.reset_view_button.setToolTip("Reset the 3D preview camera to the default orbit.")
        self.reset_view_button.clicked.connect(self.preview.reset_view)
        self.pin_texture_button = QPushButton("Pin Texture")
        self.pin_texture_button.clicked.connect(self.pin_selected_texture)
        self.clear_pin_button = QPushButton("Clear Pin")
        self.clear_pin_button.clicked.connect(self.clear_pinned_texture)
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Export the selected asset, or export a tree folder as a ZIP when a folder row is selected.")
        self.export_button.clicked.connect(self.export_selected_smart)
        self.preview.action_layout.addWidget(self.reset_view_button)
        self.preview.action_layout.addWidget(self.export_button)

        self.preview_inspector = QWidget()
        inspector_layout = QVBoxLayout(self.preview_inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.setSpacing(4)
        inspector_layout.addWidget(self.preview_inspector_tabs, stretch=1)
        self.preview_pin_row = QWidget()
        pin_layout = QHBoxLayout(self.preview_pin_row)
        pin_layout.setContentsMargins(0, 0, 0, 0)
        for button in (self.pin_texture_button, self.clear_pin_button):
            pin_layout.addWidget(button)
        pin_layout.addStretch()
        inspector_layout.addWidget(self.preview_pin_row)
        self.preview_pin_row.hide()

        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.addWidget(self.preview)
        right_splitter.addWidget(self.preview_inspector)
        right_splitter.setSizes([500, 230])

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right_splitter)
        splitter.setSizes([820, 520])

        browser_layout = QVBoxLayout(self.browser_workspace)
        browser_layout.setContentsMargins(0, 0, 0, 0)
        browser_layout.addWidget(splitter)

        self.workspace_stack = QStackedWidget()
        self.workspace_stack.addWidget(self.browser_workspace)
        self.setCentralWidget(self.workspace_stack)
        self.statusBar().showMessage("Ready")

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._auto_preview_selected)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(self.apply_filter)
        self._filter_worker: FilterWorker | None = None
        self._filter_generation = 0
        self._visible_assets_version = 0
        self._browser_tab_versions: dict[int, int] = {}
        self._asset_search_text: dict[str, str] = {}
        self._mapped_tree_parts_by_id: dict[str, tuple[str, ...]] = {}
        self._raw_tree_parts_by_id: dict[str, tuple[str, ...]] = {}

        preview_menu = menubar.addMenu("&Preview")
        self.texture_action = QAction("Textures", self)
        self.texture_action.setCheckable(True)
        self.texture_action.setChecked(self.preview.textures_toggle.isChecked())
        self.texture_action.setToolTip("Show decoded/applied texture colors in the 3D preview.")
        self.texture_action.toggled.connect(self.preview.textures_toggle.setChecked)
        self.preview.textures_toggle.toggled.connect(self.texture_action.setChecked)
        preview_menu.addAction(self.texture_action)

        self.wireframe_action = QAction("Wireframe", self)
        self.wireframe_action.setCheckable(True)
        self.wireframe_action.setChecked(self.preview.wireframe_toggle.isChecked())
        self.wireframe_action.setToolTip("Draw triangle edges over the preview mesh.")
        self.wireframe_action.toggled.connect(self.preview.wireframe_toggle.setChecked)
        self.preview.wireframe_toggle.toggled.connect(self.wireframe_action.setChecked)
        preview_menu.addAction(self.wireframe_action)

        self._style_chrome_controls()
        self.pagination_row.setVisible(False)
        self._update_pagination_bar()
        if hasattr(self, "_update_preview_details"):
            self._update_preview_details(None)
        if hasattr(self, "install_easyfind_actions"):
            self.install_easyfind_actions()

    def _style_chrome_controls(self) -> None:
        for button in (
            self.page_prev_button,
            self.page_next_button,
            self.info_copy_button,
            self.info_clear_button,
            self.reset_view_button,
            self.export_button,
        ):
            button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.show_types_button.setStyleSheet(DROPDOWN_BUTTON_STYLE)
        for child in self.preview.findChildren(QPushButton):
            if child not in {self.preview.textures_toggle, self.preview.wireframe_toggle}:
                child.setStyleSheet(CHROME_BUTTON_STYLE)

    def _make_icon_tool_button(self, tooltip: str, glyph: str) -> QToolButton:
        button = QToolButton(self)
        button.setToolTip(tooltip)
        button.setText(glyph)
        button.setFixedSize(30, 26)
        button.setStyleSheet(CHROME_BUTTON_STYLE)
        return button

    def _copy_info_panel_contents(self) -> None:
        widget = self.info_tabs.currentWidget()
        text = ""
        if widget in {self.details, self.log_box}:
            text = widget.toPlainText()
        if text.strip():
            QGuiApplication.clipboard().setText(text)
            self._update_status("Copied panel contents to clipboard.")
        else:
            self._update_status("Nothing to copy in this panel.")

    def _clear_info_panel_contents(self) -> None:
        widget = self.info_tabs.currentWidget()
        if widget is self.details:
            widget.clear()
        elif widget is self.log_box:
            widget.clear()
        self._update_status("Cleared the current info panel.")

    def _focus_terminal(self, *, banner: str | None = None) -> None:
        if hasattr(self, "info_tabs") and hasattr(self, "log_box"):
            self.info_tabs.setCurrentWidget(self.log_box)
        if banner:
            self._update_status(banner)

    def _load_profile_summary(self) -> None:
        if not self.rom_path:
            self.profile_text = ""
            return
        try:
            rom = NDSRom.from_path(self.rom_path)
            self.rom_game_code = (rom.info.game_code or "").strip().upper()[:4]
            self.rom_title = (rom.info.title or "").strip()
            files = list(rom.iter_files())
            profile = detect_profile(rom.info.title, rom.info.game_code, [f.path for f in files])
            mapping = choose_mapping(
                rom.info.title,
                rom.info.game_code,
                available=load_mappings(platform="nds"),
            )
            self.current_mapping = mapping
            parts = [f"Profile: {profile.label} ({profile.confidence}).", mapping_summary(mapping)]
            if profile.priority_queries:
                parts.append("Useful searches: " + ", ".join(profile.priority_queries) + ".")
            if profile.priority_paths:
                parts.append("Priority paths/filters: " + ", ".join(profile.priority_paths) + ".")
            if profile.notes:
                parts.append("Notes:\n" + "\n".join(f"- {note}" for note in profile.notes))
            self.profile_text = "\n".join(parts)
        except Exception:
            self.current_mapping = None
            self.profile_text = ""
            self.rom_game_code = ""
            self.rom_title = ""

    def _update_status(self, text: str) -> None:
        # Keep the status bar short so it never steals browser/terminal space.
        first_line = str(text).splitlines()[0] if text else ""
        if len(first_line) > 140:
            first_line = first_line[:137] + "..."
        self.statusBar().showMessage(first_line, 7000)
        if hasattr(self, "log_box"):
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_box.append(f"[{timestamp}] {text}")
