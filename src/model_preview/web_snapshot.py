"""Main-thread three.js snapshots for EasyFind model thumbnails."""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from PySide6.QtCore import (
    QByteArray,
    QObject,
    QThread,
    QMetaObject,
    Qt,
    Q_ARG,
    Q_RETURN_ARG,
    QTimer,
    Slot,
)
from PySide6.QtWidgets import QApplication, QVBoxLayout, QWidget

from ..ui.preview.web_preview import WebGlbPreviewWidget, webengine_preview_available


class ModelWebSnapshotService(QObject):
    """Embeds the three.js viewer in the EasyFind build UI during model thumbnail bakes."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._available = webengine_preview_available()
        self._widget: WebGlbPreviewWidget | None = None
        self._host: QWidget | None = None
        self._staging_root = Path(tempfile.gettempdir()) / "rae_easyfind_snap"

    def _stage_preview_dir_for_capture(self, glb_path: Path) -> Path:
        """Copy the worker-prepared preview folder onto the GUI thread before HTTP load."""
        source_dir = glb_path.resolve().parent
        self._staging_root.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(str(source_dir).encode()).hexdigest()[:16]
        dest_dir = self._staging_root / key
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        shutil.copytree(source_dir, dest_dir)
        return dest_dir / glb_path.name

    def begin_session(self, host: QWidget) -> bool:
        """Attach the viewer inside the main window; call before model thumbnail baking."""
        if not self._available:
            return False
        self.end_session()
        self._host = host
        layout = host.layout()
        if layout is None:
            layout = QVBoxLayout(host)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            host.setLayout(layout)
        self._widget = WebGlbPreviewWidget(host)
        self._widget.setFixedSize(256, 256)
        layout.addWidget(self._widget, alignment=Qt.AlignmentFlag.AlignCenter)
        host.show()
        self._widget.wait_until_api_ready()
        return self._widget.is_available()

    def end_session(self) -> None:
        """Hide and tear down the embedded viewer after baking completes."""
        if self._widget is not None:
            self._widget.setParent(None)
            self._widget.deleteLater()
            self._widget = None
        if self._host is not None:
            self._host.hide()
            self._host = None

    def is_available(self) -> bool:
        return (
            self._available
            and self._widget is not None
            and self._widget.is_available()
        )

    def _capture_impl(
        self,
        glb_path: Path,
        width: int,
        height: int,
        *,
        yaw_deg: float = 35.0,
        pitch_deg: float = 28.0,
        zoom_factor: float = 1.0,
        output_width: int | None = None,
        output_height: int | None = None,
    ) -> bytes | None:
        if self._widget is None:
            return None
        from ..easyfind.model_thumbnail import finalize_easyfind_model_thumbnail

        staged_glb = self._stage_preview_dir_for_capture(glb_path)
        png = None
        for attempt in range(2):
            png = self._widget.capture_snapshot_png(
                staged_glb,
                width,
                height,
                yaw_deg=yaw_deg,
                pitch_deg=pitch_deg,
                zoom_factor=zoom_factor,
                opaque_gray=False,
                timeout_ms=90000 if attempt else 60000,
            )
            if png:
                break
        if not png:
            return None
        out_w = output_width if output_width is not None else width
        out_h = output_height if output_height is not None else height
        if out_w == width and out_h == height:
            return png
        return finalize_easyfind_model_thumbnail(png, output_size=out_w)

    @Slot(str, int, int, float, float, float, int, int, result=QByteArray)
    def capture_png_bytes(
        self,
        glb_path: str,
        width: int,
        height: int,
        yaw_deg: float,
        pitch_deg: float,
        zoom_factor: float,
        output_width: int,
        output_height: int,
    ) -> QByteArray:
        data = self._capture_impl(
            Path(glb_path),
            width,
            height,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
            zoom_factor=zoom_factor,
            output_width=output_width,
            output_height=output_height,
        )
        return QByteArray(data) if data else QByteArray()

    def capture_blocking(
        self,
        glb_path: Path,
        width: int,
        height: int,
        *,
        yaw_deg: float = 35.0,
        pitch_deg: float = 28.0,
        zoom_factor: float = 1.0,
        output_width: int | None = None,
        output_height: int | None = None,
    ) -> bytes | None:
        """Safe to call from EasyFind worker threads; runs three.js on the GUI thread."""
        if not self.is_available():
            return None
        app = QApplication.instance()
        if app is None:
            return None
        if QThread.currentThread() is app.thread():
            return self._capture_impl(
                glb_path,
                width,
                height,
                yaw_deg=yaw_deg,
                pitch_deg=pitch_deg,
                zoom_factor=zoom_factor,
                output_width=output_width,
                output_height=output_height,
            )
        raw = QMetaObject.invokeMethod(
            self,
            "capture_png_bytes",
            Qt.ConnectionType.BlockingQueuedConnection,
            Q_RETURN_ARG(QByteArray),
            Q_ARG(str, str(glb_path.resolve())),
            Q_ARG(int, int(width)),
            Q_ARG(int, int(height)),
            Q_ARG(float, float(yaw_deg)),
            Q_ARG(float, float(pitch_deg)),
            Q_ARG(float, float(zoom_factor)),
            Q_ARG(int, int(output_width if output_width is not None else width)),
            Q_ARG(int, int(output_height if output_height is not None else height)),
        )
        if not raw:
            return None
        if isinstance(raw, QByteArray):
            return bytes(raw) if len(raw) else None
        if isinstance(raw, bytes):
            return raw or None
        return None
