"""Qt WebEngine + three.js GLB preview."""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path

from PySide6.QtCore import QEventLoop, QBuffer, QByteArray, QIODevice, QTimer, QUrl, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget, QApplication

from .preview_server import get_preview_server

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_VIEWER_HTML = _STATIC_DIR / "glb_viewer.html"


def webengine_preview_available() -> bool:
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView  # noqa: F401

        return _VIEWER_HTML.is_file()
    except Exception:
        return False


def _configure_web_settings(page) -> None:
    from PySide6.QtWebEngineCore import QWebEngineSettings

    settings = page.settings()
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
    settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)


def _png_from_data_url(data_url: object) -> bytes | None:
    if not isinstance(data_url, str) or not data_url.startswith("data:image/png;base64,"):
        return None
    try:
        return base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return None


class WebGlbPreviewWidget(QWidget):
    """Embedded glTF viewer that honors extras.rae.renderClass."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._background_name = "Checkered"
        self._last_glb_path: Path | None = None
        self._preview_platform_id = "nds"
        self._available = webengine_preview_available()
        self._page_ready = False
        self._api_ready = False
        self._pending_js: list[str] = []
        self._server = get_preview_server() if self._available else None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if self._available:
            from PySide6.QtWebEngineWidgets import QWebEngineView

            self._view = QWebEngineView(self)
            self._view.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
            _configure_web_settings(self._view.page())
            self._view.page().loadFinished.connect(self._on_page_load_finished)
            self._view.load(QUrl(self._server.viewer_url(platform_id=self._preview_platform_id)))
            layout.addWidget(self._view)
            self._health = QLabel(self)
            self._health.setStyleSheet(
                "color: #ffaac8; background: rgba(0,0,0,0.72); padding: 8px; font: 12px sans-serif;"
            )
            self._health.hide()
            self._health.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
            QTimer.singleShot(8000, self._report_viewer_health)
        else:
            self._view = QLabel("WebEngine preview unavailable.", self)
            self._view.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._view.setWordWrap(True)
            layout.addWidget(self._view)

    def is_available(self) -> bool:
        return self._available

    def set_preview_platform(self, platform_id: str) -> None:
        platform_id = (platform_id or "nds").strip() or "nds"
        if platform_id == self._preview_platform_id and self._page_ready and self._api_ready:
            return
        self._preview_platform_id = platform_id
        self._page_ready = False
        self._api_ready = False
        self._pending_js.clear()
        self._view.load(QUrl(self._server.viewer_url(platform_id=platform_id)))

    def load_glb(self, path: Path, *, preview_platform_id: str | None = None) -> None:
        if not self._available or self._server is None:
            return
        if preview_platform_id:
            self.set_preview_platform(preview_platform_id)
        self._last_glb_path = path.resolve()
        url = self._server.model_url(self._last_glb_path)
        self._run_when_api_ready(
            f"window.raeGlbPreview.load({_js_string(url)}).catch(function(err){{"
            f"var el=document.getElementById('error');if(el){{el.textContent=String(err);el.style.display='block';}}}});"
        )

    def set_background_name(self, name: str) -> None:
        self._background_name = name if name in {"White", "Checkered", "Black"} else "Checkered"
        if not self._available:
            return
        self._run_when_api_ready(f"window.raeGlbPreview.setBackground({_js_string(self._background_name)});")

    def set_wireframe(self, enabled: bool) -> None:
        if not self._available:
            return
        flag = "true" if enabled else "false"
        self._run_when_api_ready(f"window.raeGlbPreview.setWireframe({flag});")

    def reset_view(self) -> None:
        if not self._available:
            return
        self._run_when_api_ready("window.raeGlbPreview.resetView();")

    def play_animation(self, name: str) -> None:
        if not self._available:
            return
        self._run_when_api_ready(f"window.raeGlbPreview.playAnimation({_js_string(name)});")

    def stop_animation(self) -> None:
        if not self._available:
            return
        self._run_when_api_ready("window.raeGlbPreview.stopAnimation();")

    def set_eye_expression_frame(self, material_name: str, frame_index: int) -> None:
        if not self._available:
            return
        self._run_when_api_ready(
            f"window.raeGlbPreview.setEyeExpressionFrame({_js_string(material_name)}, {int(frame_index)});"
        )

    def set_texture_variant(self, variant_id: str) -> None:
        if not self._available:
            return
        variant = "shiny" if str(variant_id).strip().lower() == "shiny" else "normal"
        self._run_when_api_ready(
            f"window.raeGlbPreview.setTextureVariant({_js_string(variant)});"
        )

    def set_form_variant(self, form_id: str) -> None:
        if not self._available:
            return
        self._run_when_api_ready(
            f"window.raeGlbPreview.setFormVariant({_js_string(str(form_id))});"
        )

    def set_texture_frame(self, material_name: str, offset_u: float, offset_v: float) -> None:
        """Deprecated: use set_eye_expression_frame with a 0-based frame index."""
        if not self._available:
            return
        self._run_when_api_ready(
            f"window.raeGlbPreview.setEyeExpressionFrame({_js_string(material_name)}, {int(offset_u)});"
        )

    def start_flipbook(self, clips_json: str) -> None:
        if not self._available:
            return
        self._run_when_api_ready(f"window.raeGlbPreview.startFlipbook({clips_json});")

    def pause_flipbook(self) -> None:
        if not self._available:
            return
        self._run_when_api_ready("window.raeGlbPreview.pauseFlipbook();")

    def clear_scene(self) -> None:
        """Drop the current model without reloading the viewer page."""
        self._last_glb_path = None
        if self._available:
            self._run_when_api_ready("window.raeGlbPreview.clearScene();")

    def clear(self) -> None:
        self.clear_scene()

    def _on_page_load_finished(self, ok: bool) -> None:
        self._page_ready = bool(ok)
        if not ok:
            return
        self._poll_api_ready(attempt=0)

    def _poll_api_ready(self, *, attempt: int = 0) -> None:
        if not self._available:
            return

        def _check(result) -> None:
            if result:
                self._api_ready = True
                if hasattr(self, "_health"):
                    self._health.hide()
                pending = list(self._pending_js)
                self._pending_js.clear()
                for js in pending:
                    self._view.page().runJavaScript(js)
                if not pending:
                    if self._last_glb_path is not None:
                        self.load_glb(self._last_glb_path)
                    else:
                        self.set_background_name(self._background_name)
                return
            if attempt >= 200:
                return
            QTimer.singleShot(50, lambda: self._poll_api_ready(attempt=attempt + 1))

        self._view.page().runJavaScript("Boolean(window.raeGlbPreview)", _check)

    def _run_when_api_ready(self, js: str) -> None:
        if not self._available:
            return
        wrapped = f"(function(){{ {js} }})();"
        if not self._page_ready or not self._api_ready:
            self._pending_js.append(wrapped)
            return
        self._view.page().runJavaScript(wrapped)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_health"):
            self._health.setGeometry(8, 8, max(100, self.width() - 16), 80)

    def _report_viewer_health(self) -> None:
        if not self._available or self._api_ready:
            return
        self._health.setText(
            "3D viewer did not start.\n"
            "Try: RAE_LEGACY_GL_PREVIEW=1 ./rae run"
        )
        self._health.show()
        self._health.raise_()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._last_glb_path is not None:
            self.load_glb(self._last_glb_path)

    def wait_until_api_ready(self, *, timeout_ms: int = 20000) -> bool:
        if not self._available:
            return False
        if self._api_ready:
            return True
        loop = QEventLoop()
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(timeout_ms)

        def poll() -> None:
            if self._api_ready:
                loop.quit()
                return
            QTimer.singleShot(50, poll)

        poll()
        loop.exec()
        return self._api_ready

    def _run_javascript_sync(self, js: str, *, timeout_ms: int = 5000) -> object:
        """Run JS that returns plain data (never a Promise)."""
        if not self._available:
            return None
        loop = QEventLoop()
        holder: dict[str, object] = {"value": None}
        timer = QTimer()
        timer.setSingleShot(True)

        def finish() -> None:
            if loop.isRunning():
                loop.quit()

        def on_result(value: object) -> None:
            holder["value"] = value
            finish()

        timer.timeout.connect(finish)
        timer.start(timeout_ms)
        self._view.page().runJavaScript(js, on_result)

        def pump_events() -> None:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()
            if loop.isRunning():
                QTimer.singleShot(16, pump_events)

        pump_events()
        loop.exec()
        return holder["value"]

    def _grab_widget_png(self) -> bytes | None:
        if not self._available:
            return None
        pixmap = self._view.grab()
        if pixmap.isNull():
            return None
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buf, "PNG")
        data = bytes(ba)
        return data if len(data) > 64 else None

    def capture_snapshot_png(
        self,
        glb_path: Path,
        width: int,
        height: int,
        *,
        timeout_ms: int = 60000,
        yaw_deg: float = 35.0,
        pitch_deg: float = 28.0,
        zoom_factor: float = 1.0,
        opaque_gray: bool = False,
    ) -> bytes | None:
        """Render a GLB to PNG using the same three.js path as the live viewport."""
        del width, height, opaque_gray  # WYSIWYG: capture at widget size, not a resized pass.
        if not self._available or self._server is None:
            return None
        if not self.wait_until_api_ready(timeout_ms=min(timeout_ms, 20000)):
            return None

        url = self._server.model_url(glb_path.resolve())
        kickoff = (
            "window.raeGlbPreview.beginSnapshotCapture("
            f"{_js_string(url)}, {float(yaw_deg)}, {float(pitch_deg)}, {float(zoom_factor)}"
            ");"
        )
        self._run_javascript_sync(kickoff, timeout_ms=5000)

        deadline = time.monotonic() + (timeout_ms / 1000.0)
        job_state: dict[str, object] = {}
        while time.monotonic() < deadline:
            app = QApplication.instance()
            if app is not None:
                app.processEvents()

            raw = self._run_javascript_sync(
                "window.raeGlbPreview.getSnapshotJobState()",
                timeout_ms=3000,
            )
            if isinstance(raw, str):
                try:
                    job_state = json.loads(raw)
                except json.JSONDecodeError:
                    job_state = {}
                if job_state.get("done"):
                    png = _png_from_data_url(job_state.get("dataUrl"))
                    if png and len(png) > 64:
                        return png
                    break

            wait_loop = QEventLoop()
            QTimer.singleShot(50, wait_loop.quit)
            wait_loop.exec()

        png = self._grab_widget_png()
        return png


def _js_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def glb_file_url(path: Path) -> str:
    return get_preview_server().model_url(path)
