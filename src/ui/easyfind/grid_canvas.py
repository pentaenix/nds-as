"""Grid background canvas for the EasyFind workspace."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

# Tuned to match the reference gray grid workspace.
CANVAS_BG = QColor(42, 42, 42)
MINOR_GRID = QColor(56, 56, 56)
MAJOR_GRID = QColor(74, 74, 74)

MINOR_SPACING = 20
MAJOR_EVERY = 5


class GridCanvas(QWidget):
    """Full-area workspace background with a subtle major/minor grid."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), CANVAS_BG)

        width = self.width()
        height = self.height()
        major_step = MINOR_SPACING * MAJOR_EVERY

        minor_pen = QPen(MINOR_GRID)
        minor_pen.setWidth(1)
        painter.setPen(minor_pen)
        for x in range(0, width + 1, MINOR_SPACING):
            if x % major_step != 0:
                painter.drawLine(x, 0, x, height)
        for y in range(0, height + 1, MINOR_SPACING):
            if y % major_step != 0:
                painter.drawLine(0, y, width, y)

        major_pen = QPen(MAJOR_GRID)
        major_pen.setWidth(1)
        painter.setPen(major_pen)
        for x in range(0, width + 1, major_step):
            painter.drawLine(x, 0, x, height)
        for y in range(0, height + 1, major_step):
            painter.drawLine(0, y, width, y)

        painter.end()
        super().paintEvent(event)
