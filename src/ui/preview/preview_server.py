"""Thread-local HTTP server for the WebEngine GLB preview.

Qt WebEngine blocks ES module import maps on file:// URLs. Serving the viewer
and model directories over http://127.0.0.1 avoids that and lets GLTFLoader
fetch sibling PNG textures.
"""
from __future__ import annotations

import mimetypes
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class _PreviewHttpServer:
    def __init__(self, static_root: Path) -> None:
        self._static_root = static_root.resolve()
        self._mounts: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(self))
        self._port = int(self._httpd.server_address[1])
        self._thread = threading.Thread(target=self._httpd.serve_forever, name="rae-glb-preview", daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return self._port

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def viewer_url(self) -> str:
        return f"{self.base_url}/glb_viewer.html"

    def mount_directory(self, directory: Path) -> str:
        directory = directory.resolve()
        mount_id = secrets.token_hex(8)
        with self._lock:
            self._mounts[mount_id] = directory
        return mount_id

    def model_url(self, glb_path: Path) -> str:
        glb_path = glb_path.resolve()
        mount_id = self.mount_directory(glb_path.parent)
        return f"{self.base_url}/m/{mount_id}/{glb_path.name}"

    def resolve_mount(self, mount_id: str) -> Path | None:
        with self._lock:
            return self._mounts.get(mount_id)

    def resolve_static(self, rel_path: str) -> Path | None:
        rel = Path(unquote(rel_path.lstrip("/")))
        if rel.parts and rel.parts[0] == "..":
            return None
        candidate = (self._static_root / rel).resolve()
        if candidate == self._static_root or self._static_root in candidate.parents:
            if candidate.is_file():
                return candidate
        return None

    def shutdown(self) -> None:
        self._httpd.shutdown()


def _make_handler(server: _PreviewHttpServer):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args) -> None:
            del format, args

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = parsed.path or "/"
            if path.startswith("/m/"):
                parts = path.split("/")
                if len(parts) < 4:
                    self.send_error(404)
                    return
                mount_id = parts[2]
                rel = "/".join(parts[3:])
                base = server.resolve_mount(mount_id)
                if base is None:
                    self.send_error(404)
                    return
                file_path = (base / unquote(rel)).resolve()
                if base not in file_path.parents and file_path != base:
                    self.send_error(403)
                    return
                if not file_path.is_file():
                    self.send_error(404)
                    return
                _send_path(self, file_path)
                return

            rel_path = path.lstrip("/") or "glb_viewer.html"
            static_file = server.resolve_static(rel_path)
            if static_file is None:
                self.send_error(404)
                return
            _send_path(self, static_file)

    return Handler


def _send_path(handler: BaseHTTPRequestHandler, file_path: Path) -> None:
    data = file_path.read_bytes()
    mime, _enc = mimetypes.guess_type(str(file_path))
    if file_path.suffix.lower() == ".js":
        mime = "text/javascript"
    elif file_path.suffix.lower() == ".html":
        mime = "text/html"
    elif file_path.suffix.lower() == ".glb":
        mime = "model/gltf-binary"
    handler.send_response(200)
    handler.send_header("Content-Type", mime or "application/octet-stream")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except BrokenPipeError:
        pass


_server: _PreviewHttpServer | None = None
_server_lock = threading.Lock()


def get_preview_server() -> _PreviewHttpServer:
    global _server
    with _server_lock:
        if _server is None:
            _server = _PreviewHttpServer(_STATIC_DIR)
        return _server
