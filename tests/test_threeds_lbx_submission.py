from __future__ import annotations

import math
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from rae.platforms.threeds.gltf.glb_io import GlbData, read_glb
from rae.platforms.threeds.cgfx_preview import PreparedCgfxModel
from rae.platforms.threeds.lbx_catalog import LbxExportJob
from rae.platforms.threeds.lbx_submission import (
    _copy_prepared_to_archive,
    is_lbx_upright_weapon,
    write_lbx_preview_glb,
)


def test_weapon_preview_rotation_wraps_scene_without_changing_model_nodes(tmp_path: Path) -> None:
    source = tmp_path / "source.glb"
    destination = tmp_path / "preview.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"name": "weapon", "mesh": 0}],
            "meshes": [{"primitives": []}],
            "buffers": [{"byteLength": 4}],
        },
        bin_chunk=b"test",
    ).write(source)

    write_lbx_preview_glb(source, destination, x_rotation_deg=-90.0)

    preview = read_glb(destination)
    assert preview.bin_chunk == b"test"
    assert preview.json["nodes"][0] == {"name": "weapon", "mesh": 0}
    assert preview.json["scenes"][0]["nodes"] == [1]
    wrapper = preview.json["nodes"][1]
    assert wrapper["children"] == [0]
    assert wrapper["rotation"] == [
        pytest.approx(-math.sqrt(0.5)),
        0.0,
        0.0,
        pytest.approx(math.sqrt(0.5)),
    ]


def test_zero_preview_roll_copies_glb_unchanged(tmp_path: Path) -> None:
    source = tmp_path / "source.glb"
    destination = tmp_path / "preview.glb"
    GlbData(
        json={"asset": {"version": "2.0"}, "scenes": [{"nodes": [0]}], "nodes": [{}]},
        bin_chunk=b"",
    ).write(source)

    write_lbx_preview_glb(source, destination)

    assert destination.read_bytes() == source.read_bytes()


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/3ddata/wpn/wpn_sh_sq05_00.bcmdl", True),
        ("/3ddata/wpn/wpn_fi_fi02_00.bcmdl", True),
        ("/3ddata/wpn/wpn_fi_ku02_00.bcmdl", False),
        ("/3ddata/wpn/wpn_gu_ha04_00.bcmdl", False),
    ],
)
def test_upright_weapon_profile_selects_only_shields_and_claws(
    path: str,
    expected: bool,
) -> None:
    job = LbxExportJob(path, ("Weapons", "Test"), "Test")
    assert is_lbx_upright_weapon(job) is expected


def test_archive_renames_conflicting_variant_texture_and_updates_dae(tmp_path: Path) -> None:
    archive = tmp_path / "archive"
    archive.mkdir()
    first_texture = tmp_path / "first" / "shared.png"
    second_texture = tmp_path / "second" / "shared.png"
    first_texture.parent.mkdir()
    second_texture.parent.mkdir()
    first_texture.write_bytes(b"normal")
    second_texture.write_bytes(b"custom")

    def prepared(folder: str, texture: Path) -> PreparedCgfxModel:
        dae = tmp_path / f"{folder}.dae"
        dae.write_text(
            '<?xml version="1.0"?><COLLADA><library_images><image>'
            '<init_from>shared.png</init_from></image></library_images>'
            '<library_geometries><geometry/></library_geometries></COLLADA>',
            encoding="utf-8",
        )
        return PreparedCgfxModel(tmp_path / "unused.glb", dae, (texture,), (), {})

    _copy_prepared_to_archive(prepared("normal", first_texture), archive, "Model.dae")
    _copy_prepared_to_archive(
        prepared("custom", second_texture),
        archive,
        "Model Custom R.dae",
        texture_variant="custom_r",
    )

    assert (archive / "shared.png").read_bytes() == b"normal"
    assert (archive / "shared_custom_r.png").read_bytes() == b"custom"
    custom_root = ET.parse(archive / "Model Custom R.dae")
    assert custom_root.findtext(".//init_from") == "shared_custom_r.png"
