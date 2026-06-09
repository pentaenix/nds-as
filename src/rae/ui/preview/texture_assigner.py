"""Coloring-book style mesh ↔ texture assignment panel for model preview."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)


@dataclass(slots=True)
class TextureOption:
    key: str
    label: str
    path: Path


class _SwatchButton(QPushButton):
    def __init__(self, *, size: int = 72):
        super().__init__()
        self.setCheckable(True)
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pixmap: QPixmap | None = None
        self.setStyleSheet(
            "QPushButton { border: 2px solid #555; border-radius: 6px; background: #2a2a2a; }"
            "QPushButton:checked { border: 2px solid #6cb6ff; }"
            "QPushButton:hover { border: 2px solid #888; }"
        )

    def set_swatch(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        if pixmap is not None and not pixmap.isNull():
            icon = pixmap.scaled(self.width() - 8, self.height() - 8, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
            self.setIcon(icon)
            self.setIconSize(icon.size())
        else:
            from PySide6.QtGui import QIcon

            self.setIcon(QIcon())
            self.setText("?")


class TextureAssignerWidget(QWidget):
    """Pick a texture, click a model part — updates preview in real time."""

    assignments_changed = Signal(dict)

    def __init__(self):
        super().__init__()
        self._asset_id: str | None = None
        self._textures: list[TextureOption] = []
        self._parts: list[str] = []
        self._assignments: dict[str, str] = {}
        self._selected_texture_key: str | None = None
        self._texture_buttons: dict[str, _SwatchButton] = {}
        self._part_buttons: dict[str, _SwatchButton] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        self._hint = QLabel(
            "Pick a texture on the left, then click a model part on the right. "
            "Changes preview instantly and save with your session."
        )
        self._hint.setWordWrap(True)
        self._hint.setStyleSheet("color: #aaa; font-size: 11px;")
        root.addWidget(self._hint)

        columns = QHBoxLayout()
        columns.setSpacing(8)

        tex_col = QVBoxLayout()
        tex_col.addWidget(self._section_label("Textures"))
        self._texture_scroll = QScrollArea()
        self._texture_scroll.setWidgetResizable(True)
        self._texture_host = QWidget()
        self._texture_grid = QGridLayout(self._texture_host)
        self._texture_grid.setContentsMargins(0, 0, 0, 0)
        self._texture_grid.setSpacing(6)
        self._texture_scroll.setWidget(self._texture_host)
        tex_col.addWidget(self._texture_scroll, stretch=1)
        columns.addLayout(tex_col, stretch=1)

        parts_col = QVBoxLayout()
        parts_col.addWidget(self._section_label("Model parts"))
        self._parts_scroll = QScrollArea()
        self._parts_scroll.setWidgetResizable(True)
        self._parts_host = QWidget()
        self._parts_grid = QGridLayout(self._parts_host)
        self._parts_grid.setContentsMargins(0, 0, 0, 0)
        self._parts_grid.setSpacing(6)
        self._parts_scroll.setWidget(self._parts_host)
        parts_col.addWidget(self._parts_scroll, stretch=1)
        columns.addLayout(parts_col, stretch=1)

        root.addLayout(columns, stretch=1)

        actions = QHBoxLayout()
        self._reset_button = QPushButton("Reset assignments")
        self._reset_button.clicked.connect(self._reset_assignments)
        actions.addWidget(self._reset_button)
        actions.addStretch()
        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888; font-size: 11px;")
        actions.addWidget(self._status_label)
        root.addLayout(actions)

        self._empty_label = QLabel("Open a model preview to use the texture assigner.")
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setWordWrap(True)
        self._empty_label.setStyleSheet("color: #777; padding: 24px;")
        root.addWidget(self._empty_label)
        self._set_ui_state(model_open=False, has_textures=False, has_parts=False)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet("font-weight: 600; color: #ddd;")
        return label

    def set_context(
        self,
        *,
        asset_id: str | None,
        parts: list[str],
        textures: list[TextureOption],
        assignments: dict[str, str] | None = None,
    ) -> None:
        self._asset_id = asset_id
        self._parts = list(parts)
        self._textures = list(textures)
        self._assignments = dict(assignments or {})
        self._selected_texture_key = None
        self._rebuild()
        model_open = bool(asset_id)
        has_textures = bool(textures)
        has_parts = bool(parts)
        self._set_ui_state(model_open=model_open, has_textures=has_textures, has_parts=has_parts)
        if model_open and has_parts:
            saved = sum(1 for p in parts if p in self._assignments)
            if has_textures:
                self._status_label.setText(f"{saved}/{len(parts)} parts assigned")
            else:
                self._status_label.setText("Waiting for texture PNGs — try Set Textures if this stays empty.")
        elif model_open:
            self._status_label.setText("Waiting for model mesh parts from preview…")

    def current_assignments(self) -> dict[str, str]:
        return dict(self._assignments)

    def asset_id(self) -> str | None:
        return self._asset_id

    def _set_ui_state(self, *, model_open: bool, has_textures: bool, has_parts: bool) -> None:
        self._hint.setVisible(model_open)
        self._texture_scroll.setVisible(model_open and has_textures)
        self._parts_scroll.setVisible(model_open and has_parts)
        self._reset_button.setVisible(model_open and has_textures and has_parts)
        self._status_label.setVisible(model_open)
        self._empty_label.setVisible(not model_open)

    def _rebuild(self) -> None:
        self._clear_grid(self._texture_grid, self._texture_buttons)
        self._clear_grid(self._parts_grid, self._part_buttons)

        tex_cols = 3
        for idx, option in enumerate(self._textures):
            cell = QWidget()
            layout = QVBoxLayout(cell)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(2)
            btn = _SwatchButton(size=68)
            btn.set_swatch(self._load_thumb(option.path))
            btn.clicked.connect(lambda checked, key=option.key: self._select_texture(key))
            layout.addWidget(btn, alignment=Qt.AlignHCenter)
            name = QLabel(option.label)
            name.setAlignment(Qt.AlignHCenter)
            name.setWordWrap(True)
            name.setStyleSheet("font-size: 10px; color: #bbb;")
            layout.addWidget(name)
            self._texture_buttons[option.key] = btn
            self._texture_grid.addWidget(cell, idx // tex_cols, idx % tex_cols)

        part_cols = 2
        for idx, part in enumerate(self._parts):
            cell = QFrame()
            cell.setFrameShape(QFrame.Shape.StyledPanel)
            cell.setStyleSheet("QFrame { background: #242424; border: 1px solid #444; border-radius: 6px; }")
            layout = QHBoxLayout(cell)
            layout.setContentsMargins(6, 6, 6, 6)
            btn = _SwatchButton(size=52)
            tex_key = self._assignments.get(part)
            if tex_key:
                path = self._path_for_key(tex_key)
                btn.set_swatch(self._load_thumb(path))
            else:
                btn.set_swatch(None)
            btn.clicked.connect(lambda checked, name=part: self._assign_to_part(name))
            layout.addWidget(btn)
            text = QVBoxLayout()
            title = QLabel(part)
            title.setStyleSheet("font-weight: 600; color: #eee;")
            subtitle = QLabel(tex_key or "No texture")
            subtitle.setStyleSheet("font-size: 10px; color: #999;")
            text.addWidget(title)
            text.addWidget(subtitle)
            layout.addLayout(text, stretch=1)
            clear = QPushButton("×")
            clear.setFixedSize(22, 22)
            clear.setToolTip("Clear assignment")
            clear.clicked.connect(lambda checked, name=part: self._clear_part(name))
            layout.addWidget(clear)
            self._part_buttons[part] = btn
            self._parts_grid.addWidget(cell, idx // part_cols, idx % part_cols)

    def _clear_grid(self, grid: QGridLayout, buttons: dict) -> None:
        buttons.clear()
        while grid.count():
            item = grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _load_thumb(self, path: Path | None) -> QPixmap | None:
        if path is None or not path.is_file():
            return None
        image = QImage(str(path))
        if image.isNull():
            return None
        image = image.convertToFormat(QImage.Format.Format_RGBA8888)
        composed = QImage(image.size(), QImage.Format.Format_RGBA8888)
        painter = QPainter(composed)
        tile = 8
        light = QColor("#555555")
        dark = QColor("#383838")
        for y in range(0, composed.height(), tile):
            for x in range(0, composed.width(), tile):
                fill = dark if ((x // tile) + (y // tile)) % 2 else light
                painter.fillRect(x, y, tile, tile, fill)
        painter.drawImage(0, 0, image)
        painter.end()
        return QPixmap.fromImage(composed).scaled(
            64,
            64,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )

    def _path_for_key(self, key: str) -> Path | None:
        for option in self._textures:
            if option.key == key:
                return option.path
        return None

    def _select_texture(self, key: str) -> None:
        self._selected_texture_key = key
        for tex_key, btn in self._texture_buttons.items():
            btn.setChecked(tex_key == key)

    def _assign_to_part(self, part: str) -> None:
        if not self._selected_texture_key:
            self._status_label.setText("Select a texture first (left column).")
            return
        self._assignments[part] = self._selected_texture_key
        self._rebuild()
        if self._selected_texture_key:
            self._select_texture(self._selected_texture_key)
        self._status_label.setText(f"Assigned {self._selected_texture_key} → {part}")
        self.assignments_changed.emit(dict(self._assignments))

    def _clear_part(self, part: str) -> None:
        self._assignments.pop(part, None)
        self._rebuild()
        if self._selected_texture_key:
            self._select_texture(self._selected_texture_key)
        self.assignments_changed.emit(dict(self._assignments))

    def _reset_assignments(self) -> None:
        self._assignments.clear()
        self._rebuild()
        self._status_label.setText("Assignments cleared.")
        self.assignments_changed.emit({})
