"""Tests for preview texture alpha prebake."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from rae.glb_policy.texture_patch import prebake_texture_alpha


def test_prebake_texture_alpha_scales_channel(tmp_path: Path) -> None:
    src = tmp_path / "glow.png"
    Image.new("RGBA", (2, 2), (255, 128, 64, 200)).save(src)
    dest = tmp_path / "baked.png"
    prebake_texture_alpha(src, dest, material_alpha=0.5)
    baked = Image.open(dest).convert("RGBA")
    assert baked.getchannel("A").getextrema() == (100, 100)
