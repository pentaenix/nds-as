"""3DS GLB exporter eye material tagging."""
from __future__ import annotations

import pytest

from rae.platforms.threeds.gf import GfMaterial
from rae.platforms.threeds.glb import _attach_material_extras, _material_role


def test_material_role_mapping():
    assert _material_role("Eye") == "eye_sclera"
    assert _material_role("AEye") == "eye_sclera"
    assert _material_role("BEye") == "eye_sclera"
    assert _material_role("CEye") == "eye_sclera"
    assert _material_role("Eye00") == "eye_sclera"
    assert _material_role("LEye03") == "eye_sclera"
    assert _material_role("EyeA") == "eye_sclera"
    assert _material_role("LIris") == "eye_iris"
    assert _material_role("RIris") == "eye_iris"
    assert _material_role("EyeInc") is None
    assert _material_role("EffWaitB_Eye_Add") is None
    assert _material_role("BodyA") is None


def test_attach_material_extras_eye_sheet():
    entry: dict = {"name": "Eye"}
    mat = GfMaterial(name="Eye", texture_names=[], texture_units=[])
    _attach_material_extras(
        entry,
        mat,
        "opaque",
        uv_unit=(2.0, 1.0, 1.0, 0.0),
        wrap_uv=(3, 2),
    )
    rae = entry["extras"]["rae"]
    assert rae["materialRole"] == "eye_sclera"
    assert rae["eyeSheet"]["cols"] == 2
    assert rae["eyeSheet"]["translation"] == [1.0, 0.0]
    assert "bindOffset" not in rae["eyeSheet"]
    expr = rae["eyeExpression"]
    assert expr["frameCount"] == 8
    assert expr["defaultFrame"] == 0
    assert len(expr["frameOffsets"]) == 8
    assert expr["frameOffsets"][0] == pytest.approx([0.0, 0.0])
    assert expr["frameOffsets"][1] == pytest.approx([1.0, 0.0])
    assert expr["frameTranslations"][0] == pytest.approx([1.0, 0.0])
    assert rae["eyeSheet"]["uvLayout"] == "raw_u"
