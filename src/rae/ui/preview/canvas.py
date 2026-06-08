"""Preview subcomponent."""
from __future__ import annotations

from PySide6.QtCore import Qt, QEvent, QSize
from PySide6.QtGui import QBrush, QPainter, QPalette, QWheelEvent
from PySide6.QtWidgets import QFrame, QSizePolicy

from ..constants import CHECKER_DARK, CHECKER_LIGHT

class PreviewCanvas(QFrame):
    """Painted viewport background for empty/image preview modes."""

    def __init__(self, preview: "PreviewWidget"):
        super().__init__()
        self._preview = preview
        self.setFrameShape(QFrame.NoFrame)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        name = self._preview._background_name
        if name == "Checkered":
            painter.fillRect(self.rect(), self._preview._checkered_brush)
        elif name == "Black":
            painter.fillRect(self.rect(), QColor("#141414"))
        else:
            painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.end()


try:
    from OpenGL import GL as _GL
    import pyqtgraph.opengl as gl

    class PreviewGLView(gl.GLViewWidget):
        """GL viewport that paints the checker/solid background inside OpenGL."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._preview_background_name = "Checkered"

        def set_preview_background_name(self, name: str) -> None:
            self._preview_background_name = name if name in {"White", "Checkered", "Black"} else "Checkered"
            self.update()

        def paint(self, *, region, viewport, useItemNames=False):
            name = self._preview_background_name
            if name == "Checkered":
                self._paint_checker(viewport)
            else:
                rgba = {
                    "White": (1.0, 1.0, 1.0, 1.0),
                    "Black": (0.08, 0.08, 0.08, 1.0),
                }.get(name, (0.28, 0.28, 0.28, 1.0))
                _GL.glClearColor(*rgba)
                _GL.glClear(_GL.GL_COLOR_BUFFER_BIT | _GL.GL_DEPTH_BUFFER_BIT)
            self.setProjection(region, viewport)
            self.setModelview()
            _GL.glClear(_GL.GL_DEPTH_BUFFER_BIT)
            self.drawItemTree(useItemNames=useItemNames)

        def _paint_checker(self, viewport) -> None:
            _x, _y, width, height = viewport
            tile = 14
            light = _qcolor_rgbf("#4a4a4a")
            dark = _qcolor_rgbf("#353535")
            _GL.glDisable(_GL.GL_DEPTH_TEST)
            _GL.glDisable(_GL.GL_LIGHTING)
            _GL.glMatrixMode(_GL.GL_PROJECTION)
            _GL.glPushMatrix()
            _GL.glLoadIdentity()
            _GL.glOrtho(0, width, height, 0, -1, 1)
            _GL.glMatrixMode(_GL.GL_MODELVIEW)
            _GL.glPushMatrix()
            _GL.glLoadIdentity()
            for y in range(0, height + tile, tile):
                for x in range(0, width + tile, tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        _GL.glColor4f(*dark)
                    else:
                        _GL.glColor4f(*light)
                    x2 = min(x + tile, width)
                    y2 = min(y + tile, height)
                    _GL.glBegin(_GL.GL_QUADS)
                    _GL.glVertex2f(x, y)
                    _GL.glVertex2f(x2, y)
                    _GL.glVertex2f(x2, y2)
                    _GL.glVertex2f(x, y2)
                    _GL.glEnd()
            _GL.glPopMatrix()
            _GL.glMatrixMode(_GL.GL_PROJECTION)
            _GL.glPopMatrix()
            _GL.glMatrixMode(_GL.GL_MODELVIEW)
            _GL.glEnable(_GL.GL_DEPTH_TEST)

except Exception:
    PreviewGLView = None  # type: ignore[misc, assignment]

