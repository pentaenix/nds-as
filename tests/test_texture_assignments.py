import json
import struct
from pathlib import Path

from rae.core.texture_assignments import relevant_assigner_texture_paths


def _write_minimal_glb(path: Path, gltf: dict) -> None:
    json_bytes = json.dumps(gltf).encode("utf-8")
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes))
    chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    path.write_bytes(header + chunk)


def test_relevant_assigner_textures_keep_animation_frames(tmp_path: Path) -> None:
    used = tmp_path / "sign_a.png"
    frame1 = tmp_path / "sign_a.1.png"
    frame2 = tmp_path / "sign_a.2.png"
    other = tmp_path / "unrelated_sheet_tile.png"
    for path in (used, frame1, frame2, other):
        path.write_bytes(b"png")

    paths = relevant_assigner_texture_paths(
        fallback_paths=[used, frame1, frame2, other],
        texture_by_name={},
        mesh_texture_paths=[used],
        material_to_texture={},
        assignments={},
        glb_path=None,
    )
    names = {p.name for p in paths}
    assert names == {"sign_a.png", "sign_a.1.png", "sign_a.2.png"}


def test_relevant_assigner_textures_exclude_unrelated_archive_entries(tmp_path: Path) -> None:
    gate = tmp_path / "gs_gate_a.png"
    clutter = tmp_path / "gs_other_thing.png"
    gate.write_bytes(b"png")
    clutter.write_bytes(b"png")

    paths = relevant_assigner_texture_paths(
        fallback_paths=[gate, clutter],
        texture_by_name={},
        mesh_texture_paths=[gate],
        material_to_texture={"mat_a": "gs_gate_a"},
        assignments={},
        glb_path=None,
    )
    assert [p.name for p in paths] == ["gs_gate_a.png"]


def test_relevant_assigner_ignores_other_pngs_in_glb_folder(tmp_path: Path) -> None:
    windmill_tex = tmp_path / "wk_sp1.png"
    pc_tex = tmp_path / "gs_pc_a.png"
    windmill_tex.write_bytes(b"png")
    pc_tex.write_bytes(b"png")
    glb = tmp_path / "windmill.glb"
    _write_minimal_glb(
        glb,
        {
            "materials": [
                {"name": "wk_sp1", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            ],
            "textures": [{"source": 0}],
            "images": [{"uri": "wk_sp1.png"}],
            "meshes": [{"primitives": [{"material": 0}]}],
        },
    )

    paths = relevant_assigner_texture_paths(
        fallback_paths=[windmill_tex, pc_tex],
        texture_by_name={"wk_sp1": windmill_tex, "gs_pc_a": pc_tex},
        mesh_texture_paths=[windmill_tex],
        material_to_texture={"wk_sp1": "wk_sp1"},
        assignments={},
        glb_path=glb,
        mesh_part_labels=["wk_sp1"],
    )
    assert {p.name for p in paths} == {"wk_sp1.png"}
