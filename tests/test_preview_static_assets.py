from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "rae" / "ui" / "preview" / "static"

REQUIRED = [
    "glb_viewer.html",
    "rae-material-policy.js",
    "texture-alpha.js",
    "vendor/three/three.module.js",
    "vendor/three/addons/loaders/GLTFLoader.js",
    "vendor/three/addons/utils/BufferGeometryUtils.js",
    "vendor/three/addons/environments/RoomEnvironment.js",
]


def test_preview_static_vendor_files_present():
    missing = [rel for rel in REQUIRED if not (STATIC / rel).is_file()]
    assert not missing, f"Missing preview static files: {missing}"
