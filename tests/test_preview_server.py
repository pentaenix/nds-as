from __future__ import annotations

import urllib.request
from pathlib import Path

from rae.ui.preview.preview_server import get_preview_server


def test_preview_server_serves_viewer_and_glb():
    server = get_preview_server()
    html = urllib.request.urlopen(server.viewer_url(), timeout=2).read()
    assert b"raeGlbPreview" in html or b"three" in html
    js = urllib.request.urlopen(f"{server.base_url}/vendor/three/three.module.js", timeout=2)
    assert js.status == 200

    sample = Path(__file__).resolve().parents[1] / "exports" / "dsm_model_07420f11bbe02f78_glb" / "en_pc.glb"
    if not sample.is_file():
        return
    glb_url = server.model_url(sample)
    payload = urllib.request.urlopen(glb_url, timeout=2).read()
    assert payload[:4] == b"glTF"
