"""NDS island GLB material classification tests."""
from __future__ import annotations

import pytest

from rae.platforms.nds.gltf.classify import RenderClass, classify_material
from rae.platforms.nds.gltf.geometry_stats import MaterialGeometryStats

pytestmark = pytest.mark.nds


def test_fractional_alpha_horizontal_becomes_uniform_decal():
    material = {
        "alphaMode": "BLEND",
        "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 0.29]},
    }
    stats = MaterialGeometryStats(triangle_count=4, horizontal_face_fraction=1.0)
    result = classify_material(material, texture_bytes=None, geometry_stats=stats)
    assert result.render_class == RenderClass.UNIFORM_DECAL


def test_fractional_alpha_vertical_stays_blend():
    material = {
        "alphaMode": "BLEND",
        "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 0.5]},
    }
    stats = MaterialGeometryStats(triangle_count=4, horizontal_face_fraction=0.0)
    result = classify_material(material, texture_bytes=None, geometry_stats=stats)
    assert result.render_class == RenderClass.BLEND


def test_mask_downgrade_when_texture_opaque_and_nitro_opaque():
    material = {
        "alphaMode": "BLEND",
        "extras": {"rae": {"nitro": {"textureAlpha": "opaque"}}},
    }
    result = classify_material(material, texture_bytes=None, geometry_stats=None)
    assert result.render_class == RenderClass.OPAQUE


def test_mask_preserved_for_nitro_transparent():
    material = {
        "alphaMode": "MASK",
        "extras": {"rae": {"nitro": {"textureAlpha": "transparent"}}},
    }
    result = classify_material(material, texture_bytes=None, geometry_stats=None)
    assert result.render_class == RenderClass.MASK


def test_mask_preserved_when_apicula_declares_mask():
    material = {"alphaMode": "MASK"}
    result = classify_material(material, texture_bytes=None, geometry_stats=None)
    assert result.render_class == RenderClass.MASK


def test_nds_classify_sets_platform_tag():
    material = {"alphaMode": "MASK"}
    from rae.platforms.nds.gltf.classify import apply_render_class_to_material

    result = classify_material(material, texture_bytes=None, geometry_stats=None)
    apply_render_class_to_material(material, result)
    assert material["extras"]["rae"]["platform"] == "nds"
