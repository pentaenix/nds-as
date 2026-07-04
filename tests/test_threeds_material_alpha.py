"""GF material alpha export — mask layers must not become glTF BLEND."""
from __future__ import annotations

from rae.platforms.threeds.gf import GfMaterial
from rae.platforms.threeds.glb import _material_nitro_alpha


def test_kurumiru_mask_layer_is_opaque():
    mat = GfMaterial(
        name="BodyBKurumiru00",
        texture_names=["pm0002_00_BodyB1.tga", "pm0002_00_BodyBMask.tga"],
        texture_units=[],
        emission=None,
        diffuse=(0, 0, 0, 51),
        specular0=None,
        blend=(255, 255, 255, 0),
    )
    assert _material_nitro_alpha(mat) == 1.0


def test_real_blend_alpha_still_used():
    mat = GfMaterial(
        name="Shadow",
        texture_names=[],
        texture_units=[],
        emission=None,
        diffuse=(255, 255, 255, 128),
        specular0=None,
        blend=(255, 255, 255, 0),
    )
    assert _material_nitro_alpha(mat) == 128 / 255.0
