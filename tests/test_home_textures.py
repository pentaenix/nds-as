from __future__ import annotations

from pathlib import Path

from rae.platforms.home.home_textures import (
    align_home_bindings_to_glb,
    build_home_texture_bindings,
    texture_sheet_entries,
)
from rae.platforms.unity.mesh_preview import _parse_obj, _write_glb


def _psyduck_textures(tmp_path: Path) -> list[Path]:
    textures = [
        tmp_path / "pm0054_00_00_Body_col.png",
        tmp_path / "pm0054_00_00_Body_emi.png",
        tmp_path / "pm0054_00_00_Eye_col.png",
    ]
    for path in textures:
        path.write_bytes(b"png")
    return textures


def test_build_home_texture_bindings_maps_body_and_eye(tmp_path):
    textures = _psyduck_textures(tmp_path)
    bindings = build_home_texture_bindings(
        "pm0054_00_00_BodySkin",
        textures,
        part_names=[
            "pm0054_00_00_BodySkin_0",
            "pm0054_00_00_BodySkin_1",
            "pm0054_00_00_BodySkin_2",
            "pm0054_00_00_BodySkin_3",
        ],
    )

    assert bindings.material_to_texture["pm0054_00_00_bodyskin"] == "pm0054_00_00_body_col"
    assert bindings.material_to_texture["pm0054_00_00_bodyskin_3"] == "pm0054_00_00_eye_col"
    assert bindings.material_to_texture["bodyskin_3"] == "pm0054_00_00_eye_col"
    assert bindings.material_to_texture["bodyskin_0"] == "pm0054_00_00_body_col"
    assert bindings.texture_bind_order[0] == "pm0054_00_00_body_col"
    assert len(bindings.fallback_paths) == 2
    assert bindings.texture_by_name["pm0054_00_00_body_col"] == textures[0]


def test_species_id_is_not_treated_as_body_group_suffix(tmp_path):
    textures = _psyduck_textures(tmp_path)
    bindings = build_home_texture_bindings("pm0054_00_00", textures)
    assert bindings.material_to_texture["pm0054_00_00"] == "pm0054_00_00_body_col"


def test_align_home_bindings_to_glb_uses_gltf_material_names(tmp_path):
    textures = _psyduck_textures(tmp_path)
    obj_text = """\
v 0 0 0
v 1 0 0
v 0 1 0
v 1 1 0
vt 0 0
vt 1 0
vt 0 1
vt 1 1
g BodySkin_0
f 1/1/1 2/2/2 3/3/3
g BodySkin_3
f 2/2/2 4/4/4 3/3/3
"""
    model = _parse_obj(obj_text)
    glb_path = tmp_path / "psyduck.glb"
    _write_glb(model, glb_path, mesh_name="BodySkin")

    bindings = build_home_texture_bindings("BodySkin", textures, part_names=[])
    aligned = align_home_bindings_to_glb(glb_path, bindings, texture_paths=textures)

    assert aligned.material_to_texture["bodyskin_0"] == "pm0054_00_00_body_col"
    assert aligned.material_to_texture["bodyskin_3"] == "pm0054_00_00_eye_col"


def test_texture_sheet_entries_label_suffixes():
    entries = texture_sheet_entries(
        [
            Path("pm0054_00_00_Body_col.png"),
            Path("pm0054_00_00_Body_emi.png"),
        ]
    )
    assert len(entries) == 2
    assert "(col)" in entries[0]["label"]
    assert "(emi)" in entries[1]["label"]
