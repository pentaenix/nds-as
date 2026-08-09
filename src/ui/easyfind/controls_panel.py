"""Floating EasyFind grouping controls."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import CHROME_BUTTON_STYLE
from ...easyfind.canvas_filters import FOCUS_OP_OFF, FOCUS_OP_OPTIONS, FOCUS_REGION_OPTIONS
from .layout import FOCUS_COLOR_OPTIONS, GROUP_BY_OPTIONS, TYPE_FILTER_OPTIONS

PANEL_STYLE = """
QFrame#easyfindGrouping {
    background-color: rgba(28, 28, 28, 168);
    border: 1px solid rgba(120, 120, 120, 90);
    border-radius: 10px;
}
QFrame#easyfindGrouping QLabel {
    color: #e8e8e8;
    background: transparent;
}
QFrame#easyfindGrouping QComboBox,
QFrame#easyfindGrouping QCheckBox {
    background-color: rgba(48, 48, 48, 200);
    color: #f0f0f0;
    border: 1px solid rgba(100, 100, 100, 120);
    border-radius: 4px;
    padding: 3px 8px;
    min-height: 22px;
}
QFrame#easyfindGrouping QComboBox::drop-down {
    width: 18px;
    border: none;
}
"""


def _color_checkbox_scroll(
    options: dict[str, str],
    *,
    skip_any: bool = True,
) -> tuple[QScrollArea, dict[str, QCheckBox]]:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setMaximumHeight(120)
    scroll.setStyleSheet("background: transparent; border: none;")
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    checks: dict[str, QCheckBox] = {}
    for key, label in options.items():
        if skip_any and key == "any":
            continue
        cb = QCheckBox(label)
        cb.setChecked(False)
        checks[key] = cb
        layout.addWidget(cb)
    layout.addStretch()
    scroll.setWidget(host)
    return scroll, checks


def _focus_op_combo(*, default_op: str = FOCUS_OP_OFF) -> QComboBox:
    combo = QComboBox()
    combo.setFixedWidth(58)
    for key, label in FOCUS_OP_OPTIONS:
        combo.addItem(label, key)
    index = combo.findData(default_op)
    combo.setCurrentIndex(index if index >= 0 else 0)
    popup = combo.view()
    if popup is not None:
        popup.setMinimumWidth(58)
    return combo


def _focus_clause_header(layout: QVBoxLayout, label: str, op_combo: QComboBox) -> None:
    row = QHBoxLayout()
    row.setSpacing(6)
    row.addWidget(op_combo, stretch=0)
    title = QLabel(label)
    title.setStyleSheet("color: #e8e8e8;")
    row.addWidget(title, stretch=1)
    layout.addLayout(row)


def _searchable_checkbox_scroll(
    *,
    max_height: int = 120,
) -> tuple[QScrollArea, QLineEdit, QWidget, dict[str, QCheckBox]]:
    search = QLineEdit()
    search.setPlaceholderText("Search…")
    search.setClearButtonEnabled(True)
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setMaximumHeight(max_height)
    scroll.setStyleSheet("background: transparent; border: none;")
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    layout.addStretch()
    scroll.setWidget(host)
    checks: dict[str, QCheckBox] = {}
    return scroll, search, host, checks


def _repopulate_map_checks(
    host: QWidget,
    checks: dict[str, QCheckBox],
    *,
    locations: list,
    tagged_location_ids: set[str],
    only_tagged: bool = True,
) -> None:
    layout = host.layout()
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.deleteLater()
    checks.clear()
    for loc in sorted(locations, key=lambda item: (item.group, item.order or 0, item.name)):
        if getattr(loc, "kind", "") != "map":
            continue
        if only_tagged and loc.location_id not in tagged_location_ids:
            continue
        cb = QCheckBox(loc.name)
        cb.setProperty("location_id", loc.location_id)
        cb.setProperty("search_text", f"{loc.name} {loc.location_id} {' '.join(loc.aliases)}".casefold())
        checks[loc.location_id] = cb
        layout.addWidget(cb)
    layout.addStretch()


def _apply_map_search_filter(checks: dict[str, QCheckBox], query: str) -> None:
    needle = query.strip().casefold()
    for cb in checks.values():
        hay = str(cb.property("search_text") or cb.text().casefold())
        cb.setVisible(not needle or needle in hay)


def _checked_colors(checks: dict[str, QCheckBox]) -> frozenset[str]:
    return frozenset(key for key, cb in checks.items() if cb.isChecked())


class EasyFindControlsPanel(QFrame):
    apply_requested = Signal()
    clear_focus_requested = Signal()
    fit_all_requested = Signal()
    reset_view_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("easyfindGrouping")
        self.setStyleSheet(PANEL_STYLE)
        self._expanded = True
        self._type_checks: dict[str, QCheckBox] = {}
        self._magic_checks: dict[str, QCheckBox] = {}
        self._primary_color_checks: dict[str, QCheckBox] = {}
        self._secondary_color_checks: dict[str, QCheckBox] = {}
        self._primary_op_combo: QComboBox | None = None
        self._secondary_op_combo: QComboBox | None = None
        self._region_op_combo: QComboBox | None = None
        self._map_op_combo: QComboBox | None = None
        self._type_op_combo: QComboBox | None = None
        self._region_color_checks: dict[str, QCheckBox] = {}
        self._map_checks: dict[str, QCheckBox] = {}
        self._map_search: QLineEdit | None = None
        self._map_scroll_host: QWidget | None = None
        self._place_section: QWidget | None = None
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(6)

        header = QHBoxLayout()
        self.toggle_button = QToolButton()
        self.toggle_button.setText("Controls ▴")
        self.toggle_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.toggle_button.clicked.connect(self._toggle)
        header.addWidget(self.toggle_button)
        header.addStretch()
        outer.addLayout(header)

        self.body = QWidget()
        self.body.setStyleSheet("background: transparent;")
        body_layout = QVBoxLayout(self.body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(8)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_organize_tab(), "Organize")
        self.tabs.addTab(self._build_focus_tab(), "Focus")
        body_layout.addWidget(self.tabs)

        apply_row = QHBoxLayout()
        self.apply_button = QPushButton("Apply to Map")
        self.apply_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.apply_button.clicked.connect(self.apply_requested.emit)
        apply_row.addWidget(self.apply_button)
        self.clear_focus_button = QPushButton("Clear Focus")
        self.clear_focus_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.clear_focus_button.clicked.connect(self._clear_focus)
        apply_row.addWidget(self.clear_focus_button)
        body_layout.addLayout(apply_row)

        row = QHBoxLayout()
        fit_btn = QPushButton("Fit All")
        fit_btn.setStyleSheet(CHROME_BUTTON_STYLE)
        fit_btn.clicked.connect(self.fit_all_requested.emit)
        reset_btn = QPushButton("Reset")
        reset_btn.setStyleSheet(CHROME_BUTTON_STYLE)
        reset_btn.clicked.connect(self.reset_view_requested.emit)
        row.addWidget(fit_btn)
        row.addWidget(reset_btn)
        body_layout.addLayout(row)

        outer.addWidget(self.body)
        self.setMinimumWidth(220)
        self.setMaximumWidth(280)

    def _build_organize_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        hint = QLabel(
            "Organize mode: pick asset types, then Apply for a cluster overview. "
            "Focus tab settings are ignored."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b8b8b8; font-size: 11px;")
        layout.addWidget(hint)

        layout.addWidget(QLabel("Group by"))
        self.group_box = QComboBox()
        for key, label in GROUP_BY_OPTIONS.items():
            self.group_box.addItem(label, key)
        layout.addWidget(self.group_box)

        layout.addWidget(QLabel("Hide formats"))
        magic_row = QHBoxLayout()
        palette_cb = QCheckBox("Palettes (RLCN)")
        palette_cb.setChecked(True)
        self._magic_checks["RLCN"] = palette_cb
        magic_row.addWidget(palette_cb)
        magic_row.addStretch()
        layout.addLayout(magic_row)

        layout.addWidget(QLabel("Show types"))
        show_scroll = QScrollArea()
        show_scroll.setWidgetResizable(True)
        show_scroll.setMaximumHeight(140)
        show_scroll.setStyleSheet("background: transparent; border: none;")
        show_host = QWidget()
        show_layout = QVBoxLayout(show_host)
        show_layout.setContentsMargins(0, 0, 0, 0)
        show_layout.setSpacing(4)
        for key, label in TYPE_FILTER_OPTIONS.items():
            if key == "all":
                continue
            cb = QCheckBox(label)
            cb.setChecked(False)
            self._type_checks[key] = cb
            show_layout.addWidget(cb)
        show_layout.addStretch()
        show_scroll.setWidget(show_host)
        layout.addWidget(show_scroll)

        layout.addWidget(QLabel("Preview filter"))
        self.renderable_box = QComboBox()
        self.renderable_box.addItem("All", "all")
        self.renderable_box.addItem("With Preview", "with_preview")
        self.renderable_box.addItem("Without Preview", "without_preview")
        layout.addWidget(self.renderable_box)
        layout.addStretch()
        return tab

    def _build_focus_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(4, 8, 4, 4)
        layout.setSpacing(8)

        hint = QLabel(
            "Focus: empty primary = any main color. Secondary = accent colors (not main). "
            "Region / Map filter assets by in-game placement (rebuild EasyFind to refresh). "
            "Click a cluster arrow to open; double-click an open cluster to close."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b8b8b8; font-size: 11px;")
        layout.addWidget(hint)

        self._primary_op_combo = _focus_op_combo()
        _focus_clause_header(layout, "Primary color", self._primary_op_combo)
        primary_scroll, self._primary_color_checks = _color_checkbox_scroll(FOCUS_COLOR_OPTIONS)
        layout.addWidget(primary_scroll)

        self._secondary_op_combo = _focus_op_combo()
        _focus_clause_header(layout, "Secondary color", self._secondary_op_combo)
        secondary_scroll, self._secondary_color_checks = _color_checkbox_scroll(FOCUS_COLOR_OPTIONS)
        layout.addWidget(secondary_scroll)

        self._place_section = QWidget()
        place_layout = QVBoxLayout(self._place_section)
        place_layout.setContentsMargins(0, 0, 0, 0)
        place_layout.setSpacing(8)

        self._region_op_combo = _focus_op_combo()
        _focus_clause_header(place_layout, "Region", self._region_op_combo)
        region_scroll, self._region_color_checks = _color_checkbox_scroll(FOCUS_REGION_OPTIONS, skip_any=False)
        place_layout.addWidget(region_scroll)

        self._map_op_combo = _focus_op_combo()
        _focus_clause_header(place_layout, "Map", self._map_op_combo)
        map_search_row = QHBoxLayout()
        self._map_search = QLineEdit()
        self._map_search.setPlaceholderText("Search maps…")
        self._map_search.setClearButtonEnabled(True)
        self._map_search.textChanged.connect(self._on_map_search_changed)
        map_search_row.addWidget(self._map_search)
        place_layout.addLayout(map_search_row)
        map_scroll, _, self._map_scroll_host, self._map_checks = _searchable_checkbox_scroll(max_height=140)
        place_layout.addWidget(map_scroll)

        layout.addWidget(self._place_section)
        self._place_section.hide()

        self._type_op_combo = _focus_op_combo()
        _focus_clause_header(layout, "Type", self._type_op_combo)
        self.focus_type_box = QComboBox()
        for key, label in TYPE_FILTER_OPTIONS.items():
            self.focus_type_box.addItem(label, key if key != "all" else "any")
        layout.addWidget(self.focus_type_box)
        layout.addStretch()
        return tab

    def _on_map_search_changed(self, text: str) -> None:
        _apply_map_search_filter(self._map_checks, text)

    def set_place_options(self, document) -> None:
        """Populate Region/Map controls from a loaded EasyFind document."""
        if self._place_section is None or self._map_scroll_host is None:
            return
        locations = list(getattr(document, "locations", []) or [])
        tagged_ids = {
            tag.location_id
            for tag in getattr(document, "asset_tags", []) or []
            if tag.location_id
        }
        has_places = bool(locations)
        self._place_section.setVisible(has_places)
        if not has_places:
            return
        _repopulate_map_checks(
            self._map_scroll_host,
            self._map_checks,
            locations=locations,
            tagged_location_ids=tagged_ids,
            only_tagged=True,
        )
        if not self._map_checks:
            _repopulate_map_checks(
                self._map_scroll_host,
                self._map_checks,
                locations=locations,
                tagged_location_ids=tagged_ids,
                only_tagged=False,
            )
        if self._map_search is not None:
            self._on_map_search_changed(self._map_search.text())

    def apply_map_filter(self, location_id: str) -> None:
        """Select a single map and clear other map checks."""
        for cb in self._map_checks.values():
            cb.setChecked(False)
        target = self._map_checks.get(location_id)
        if target is not None:
            target.setChecked(True)
        if self._map_op_combo is not None:
            index = self._map_op_combo.findData(FOCUS_OP_OFF)
            if index >= 0:
                self._map_op_combo.setCurrentIndex(index)

    def _clear_focus(self) -> None:
        for cb in self._primary_color_checks.values():
            cb.setChecked(False)
        for cb in self._secondary_color_checks.values():
            cb.setChecked(False)
        for cb in self._region_color_checks.values():
            cb.setChecked(False)
        for cb in self._map_checks.values():
            cb.setChecked(False)
        if self._map_search is not None:
            self._map_search.clear()
        if self._primary_op_combo is not None:
            self._primary_op_combo.setCurrentIndex(0)
        if self._secondary_op_combo is not None:
            self._secondary_op_combo.setCurrentIndex(0)
        if self._region_op_combo is not None:
            self._region_op_combo.setCurrentIndex(0)
        if self._map_op_combo is not None:
            self._map_op_combo.setCurrentIndex(0)
        if self._type_op_combo is not None:
            self._type_op_combo.setCurrentIndex(0)
        self.focus_type_box.setCurrentIndex(0)
        self.tabs.setCurrentIndex(1)
        self.clear_focus_requested.emit()
        self.apply_requested.emit()

    def _toggle(self) -> None:
        self._expanded = not self._expanded
        self.body.setVisible(self._expanded)
        self.toggle_button.setText("Controls ▴" if self._expanded else "Controls ▾")
        self.adjustSize()

    def current_filters(self):
        from ...easyfind.canvas_filters import FILTER_MODE_FOCUS, FILTER_MODE_ORGANIZE
        from .layout import EasyFindCanvasFilters

        focus_mode = self.tabs.currentIndex() == 1
        if focus_mode:
            return EasyFindCanvasFilters(
                group_by=self.group_box.currentData() or "color",
                filter_mode=FILTER_MODE_FOCUS,
                focus_primary_colors=_checked_colors(self._primary_color_checks),
                focus_secondary_colors=_checked_colors(self._secondary_color_checks),
                focus_primary_op=self._primary_op_combo.currentData() if self._primary_op_combo else FOCUS_OP_OFF,
                focus_secondary_op=self._secondary_op_combo.currentData() if self._secondary_op_combo else FOCUS_OP_OFF,
                focus_region_groups=_checked_colors(self._region_color_checks),
                focus_region_op=self._region_op_combo.currentData() if self._region_op_combo else FOCUS_OP_OFF,
                focus_map_ids=_checked_colors(self._map_checks),
                focus_map_op=self._map_op_combo.currentData() if self._map_op_combo else FOCUS_OP_OFF,
                focus_type_op=self._type_op_combo.currentData() if self._type_op_combo else FOCUS_OP_OFF,
                focus_type=self.focus_type_box.currentData() or "any",
            )

        shown_types = frozenset(
            key for key, cb in self._type_checks.items() if cb.isChecked()
        )
        hidden_magics = frozenset(
            key for key, cb in self._magic_checks.items() if cb.isChecked()
        )
        return EasyFindCanvasFilters(
            group_by=self.group_box.currentData() or "color",
            filter_mode=FILTER_MODE_ORGANIZE,
            renderable_filter=self.renderable_box.currentData() or "all",
            shown_types=shown_types,
            hidden_magics=hidden_magics,
        )
