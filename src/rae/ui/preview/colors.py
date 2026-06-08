"""Preview color helpers."""
from __future__ import annotations

from PySide6.QtGui import QColor


def qcolor_rgbf(hex_color: str) -> tuple[float, float, float, float]:
    color = QColor(hex_color)
    return color.redF(), color.greenF(), color.blueF(), 1.0
