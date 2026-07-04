from __future__ import annotations

from pathlib import Path

from rae.core.modules.platform_boundaries import platform_package_dir
from rae.ui.preview.preview_server import _PLATFORMS_DIR

STATIC = Path(__file__).resolve().parents[1] / "src" / "ui" / "preview" / "static"

REQUIRED = [
    "glb_viewer.html",
    "texture-alpha.js",
    "vendor/three/three.module.js",
    "vendor/three/addons/loaders/GLTFLoader.js",
    "vendor/three/addons/utils/BufferGeometryUtils.js",
    "vendor/three/addons/environments/RoomEnvironment.js",
]


def test_preview_static_vendor_files_present():
    missing = [rel for rel in REQUIRED if not (STATIC / rel).is_file()]
    assert not missing, f"Missing preview static files: {missing}"


def test_platform_preview_policy_folders_exist():
    for platform_id, folder in (("nds", "nds"), ("3ds", "threeds"), ("mobile", "mobile")):
        assert platform_package_dir(platform_id) == folder
        preview = _PLATFORMS_DIR / folder / "preview" / "material-policy.js"
        assert preview.is_file(), f"Missing {preview}"


def test_viewer_requests_stencil_buffer_for_platform_policies():
    viewer = STATIC / "glb_viewer.html"
    text = viewer.read_text(encoding="utf-8")
    assert "stencil: true" in text
