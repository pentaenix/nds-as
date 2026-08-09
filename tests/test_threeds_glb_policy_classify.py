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


def test_pica_source_alpha_blend_overrides_no_zero_alpha_heuristic():
    rgba = bytes((255, 255, 255, 96)) * (8 * 8)
    png = rgba_to_png(rgba, 8, 8)
    material = {
        "extras": {
            "rae": {
                "pica": {
                    "alphaBlendEnabled": True,
                    "sourceRgbFactor": "source_alpha",
                    "destinationRgbFactor": "one_minus_source_alpha",
                    "alphaTestEnabled": True,
                }
            }
        }
    }
    result = classify_material(material, texture_bytes=png, geometry_stats=None)
    assert result.render_class == RenderClass.BLEND


def test_pica_replacement_blend_keeps_alpha_texture_opaque():
    material = {
        "extras": {
            "rae": {
                "pica": {
                    "alphaBlendEnabled": True,
                    "sourceRgbFactor": "one",
                    "destinationRgbFactor": "zero",
                    "alphaTestEnabled": False,
                }
            }
        }
    }
    result = classify_material(material, texture_bytes=None, geometry_stats=None)
    assert result.render_class == RenderClass.OPAQUE
