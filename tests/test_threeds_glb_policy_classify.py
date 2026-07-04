"""3DS island GLB material classification tests."""
from __future__ import annotations

import pytest

from rae.platforms.threeds.gltf.classify import RenderClass, classify_material
from rae.platforms.threeds.pica import rgba_to_png

pytestmark = pytest.mark.threeds


def test_alpha_variation_without_invisible_texels_is_opaque():
    rgba = bytearray()
    for i in range(64 * 64):
        v = 34 + (i % 200)
        rgba.extend((v, v, v, v))
    png = rgba_to_png(bytes(rgba), 64, 64)
    material = {"alphaMode": "BLEND"}
    result = classify_material(material, texture_bytes=png, geometry_stats=None)
    assert result.render_class == RenderClass.OPAQUE


def test_additive_blend_mode():
    material = {
        "extras": {"rae": {"nitro": {"blendMode": "additive"}}},
    }
    result = classify_material(material, texture_bytes=None, geometry_stats=None)
    assert result.render_class == RenderClass.ADDITIVE
