"""Preview subcomponent."""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QPixmap, QPainter, QWheelEvent
from PySide6.QtWidgets import QLabel, QSizePolicy

from .colors import qcolor_rgbf

class PreviewImageLabel(QLabel):
    """Image viewport that zooms without changing layout/window size."""

    def __init__(self):
        super().__init__()
        self._preview_pixmap: QPixmap | None = None
        self._pan_x = 0
        self._pan_y = 0
        self._dragging = False
        self._drag_global_start = QPoint()
        self._pan_drag_start = (0, 0)
        self.setAlignment(Qt.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)
        self.setMinimumSize(0, 0)
        self.setMouseTracking(True)
        self.setStyleSheet("background: transparent;")

    def sizeHint(self) -> QSize:
        return QSize(0, 0)

    def minimumSizeHint(self) -> QSize:
        return QSize(0, 0)

    def can_pan(self) -> bool:
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            return False
        return self._preview_pixmap.width() > self.width() or self._preview_pixmap.height() > self.height()

    def is_panning(self) -> bool:
        return self._dragging

    def reset_pan(self) -> None:
        self._pan_x = 0
        self._pan_y = 0
        self._dragging = False
        self.unsetCursor()

    def set_preview_pixmap(self, pixmap: QPixmap | None) -> None:
        self._preview_pixmap = pixmap
        self.clamp_pan()
        self.update()

    def clear(self) -> None:
        self._preview_pixmap = None
        self.reset_pan()
        super().clear()

    def clamp_pan(self) -> None:
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            self._pan_x = 0
            self._pan_y = 0
            return
        pw = self._preview_pixmap.width()
        ph = self._preview_pixmap.height()
        ww = max(1, self.width())
        wh = max(1, self.height())
        if pw <= ww:
            self._pan_x = 0
        else:
            left = (ww - pw) // 2 + self._pan_x
            if left > 0:
                self._pan_x -= left
            right = left + pw
            if right < ww:
                self._pan_x += ww - right
        if ph <= wh:
            self._pan_y = 0
        else:
            top = (wh - ph) // 2 + self._pan_y
            if top > 0:
                self._pan_y -= top
            bottom = top + ph
            if bottom < wh:
                self._pan_y += wh - bottom

    def start_pan_at_global(self, global_pos: QPoint) -> bool:
        if not self.can_pan():
            return False
        self._dragging = True
        self._drag_global_start = QPoint(global_pos)
        self._pan_drag_start = (self._pan_x, self._pan_y)
        self.setCursor(Qt.ClosedHandCursor)
        return True

    def move_pan_at_global(self, global_pos: QPoint) -> None:
        if not self._dragging:
            return
        delta = global_pos - self._drag_global_start
        self._pan_x = self._pan_drag_start[0] + delta.x()
        self._pan_y = self._pan_drag_start[1] + delta.y()
        self.clamp_pan()
        self.update()

    def end_pan(self) -> None:
        self._dragging = False
        self._update_cursor()

    def _update_cursor(self) -> None:
        if self.can_pan():
            self.setCursor(Qt.OpenHandCursor)
        else:
            self.unsetCursor()

    def _event_global_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self.start_pan_at_global(self._event_global_pos(event)):
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._dragging:
            self.move_pan_at_global(self._event_global_pos(event))
            event.accept()
            return
        self._update_cursor()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and self._dragging:
            self.end_pan()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        if self._preview_pixmap is None or self._preview_pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setClipRect(self.rect())
        x = (self.width() - self._preview_pixmap.width()) // 2 + self._pan_x
        y = (self.height() - self._preview_pixmap.height()) // 2 + self._pan_y
        painter.drawPixmap(x, y, self._preview_pixmap)
        painter.end()

