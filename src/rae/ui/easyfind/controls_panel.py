"""Floating EasyFind grouping controls."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..constants import CHROME_BUTTON_STYLE
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
    padding: 2px 6px;
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
            "Focus mode: pick color(s), a type, or both. Organize filters are ignored. "
            "Double-click clusters to open tiles."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b8b8b8; font-size: 11px;")
        layout.addWidget(hint)

        layout.addWidget(QLabel("Primary colors"))
        primary_scroll, self._primary_color_checks = _color_checkbox_scroll(FOCUS_COLOR_OPTIONS)
        layout.addWidget(primary_scroll)

        layout.addWidget(QLabel("Secondary colors (optional)"))
        secondary_scroll, self._secondary_color_checks = _color_checkbox_scroll(FOCUS_COLOR_OPTIONS)
        layout.addWidget(secondary_scroll)

        layout.addWidget(QLabel("Type"))
        self.focus_type_box = QComboBox()
        for key, label in TYPE_FILTER_OPTIONS.items():
            self.focus_type_box.addItem(label, key if key != "all" else "any")
        layout.addWidget(self.focus_type_box)
        layout.addStretch()
        return tab

    def _clear_focus(self) -> None:
        for cb in self._primary_color_checks.values():
            cb.setChecked(False)
        for cb in self._secondary_color_checks.values():
            cb.setChecked(False)
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
                focus_primary_colors=frozenset(
                    key for key, cb in self._primary_color_checks.items() if cb.isChecked()
                ),
                focus_secondary_colors=frozenset(
                    key for key, cb in self._secondary_color_checks.items() if cb.isChecked()
                ),
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
