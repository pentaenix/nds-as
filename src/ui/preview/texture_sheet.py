"""Browse individual textures/sprites extracted from a decoded contact sheet."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .texture_assigner import _SwatchButton


@dataclass(slots=True)
class SheetEntry:
    key: str
    label: str
    path: Path


class TextureSheetWidget(QWidget):
    entry_selected = Signal(str, str)

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        self._asset_id: str | None = None
        self._entries: list[SheetEntry] = []
        self._selected_key: str | None = None
        self._buttons: dict[str, _SwatchButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._hint = QLabel(
            "Pick a decoded entry from the sheet. The viewport shows that texture full size."
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #aaa; font-size: 11px;")
        root.addWidget(self._hint)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._host = QWidget()
        self._grid = QGridLayout(self._host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self._scroll.setWidget(self._host)
        root.addWidget(self._scroll, stretch=1)

        self._status = QLabel("")
        self._status.setStyleSheet("color: #888; font-size: 11px;")
        root.addWidget(self._status)

        self._empty = QLabel("Open a multi-texture sheet preview to browse entries here.")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet("color: #777; padding: 24px;")
        root.addWidget(self._empty)

        self._set_active(False)

    def set_context(
        self,
        *,
        asset_id: str | None,
        entries: list[SheetEntry] | None = None,
        selected_key: str | None = None,
    ) -> None:
        self._asset_id = asset_id
        self._entries = list(entries or [])
        self._selected_key = selected_key
        self._rebuild()
        active = bool(asset_id and len(self._entries) >= 2)
        self._set_active(active)
        if active:
            self._status.setText(f"{len(self._entries)} sheet entries")

    def _set_active(self, active: bool) -> None:
        self._hint.setVisible(active)
        self._scroll.setVisible(active)
        self._status.setVisible(active)
        self._empty.setVisible(not active)

    def _rebuild(self) -> None:
        self._buttons.clear()
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        cols = 4
        for idx, entry in enumerate(self._entries):
            cell = QWidget()
            layout = QVBoxLayout(cell)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            btn = _SwatchButton(size=68)
            btn.setCheckable(True)
            btn.set_swatch(self._load_thumb(entry.path))
            btn.setChecked(entry.key == self._selected_key)
            btn.clicked.connect(lambda checked, key=entry.key, label=entry.label, path=entry.path: self._on_pick(key, label, path))
            layout.addWidget(btn, alignment=Qt.AlignmentFlag.AlignHCenter)
            name = QLabel(entry.label)
            name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            name.setWordWrap(True)
            name.setStyleSheet("font-size: 10px; color: #bbb;")
            layout.addWidget(name)
            self._buttons[entry.key] = btn
            self._grid.addWidget(cell, idx // cols, idx % cols)

    def _on_pick(self, key: str, label: str, path: Path) -> None:
        self._selected_key = key
        for entry_key, btn in self._buttons.items():
            btn.blockSignals(True)
            btn.setChecked(entry_key == key)
            btn.blockSignals(False)
        self.entry_selected.emit(str(path), label)

    @staticmethod
    def _load_thumb(path: Path) -> QPixmap | None:
        if not path.is_file():
            return None
        pixmap = QPixmap(str(path))
        return None if pixmap.isNull() else pixmap
