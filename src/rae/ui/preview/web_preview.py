"""Qt WebEngine + three.js GLB preview."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer, QUrl, Qt
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

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


class WebGlbPreviewWidget(QWidget):
    """Embedded glTF viewer that honors extras.rae.renderClass."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._background_name = "Checkered"
        self._last_glb_path: Path | None = None
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
            self._view.load(QUrl(self._server.viewer_url()))
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

    def load_glb(self, path: Path) -> None:
        if not self._available or self._server is None:
            return
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
            from PySide6.QtCore import QTimer

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


def _js_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def glb_file_url(path: Path) -> str:
    return get_preview_server().model_url(path)
