"""Tests for WebEngine snapshot helpers (no WebEngine required)."""
from __future__ import annotations

import base64

from rae.ui.preview.web_preview import _png_from_data_url


def test_png_from_data_url_decodes_valid_payload() -> None:
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
    data_url = f"data:image/png;base64,{payload}"
    assert _png_from_data_url(data_url) == b"\x89PNG\r\n\x1a\nfake"


def test_png_from_data_url_rejects_non_png() -> None:
    assert _png_from_data_url("data:image/jpeg;base64,abc") is None
    assert _png_from_data_url("") is None
    assert _png_from_data_url(None) is None
