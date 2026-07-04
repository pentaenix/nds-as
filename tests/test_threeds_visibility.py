"""GFMotion mesh visibility (section 6) and bind-pose defaults."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rae.platforms.threeds.motion import (
    _parse_visibility_section,
    mesh_bind_visibility,
)

ROM = Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"

from rae.platforms.threeds.rom import MODEL_GROUP_STRIDE, parse_model_header_table, read_garc_slot
from rae.platforms.threeds.service import build_model_glb


def test_parse_visibility_section_bit_packed():
    names_blob = b"\x04Ball" + b"\x04Body"
    names_count = 2
    names_length = len(names_blob)
    frames_count = 3
    sample_count = frames_count + 1  # 4 samples
    # Ball: 1010 -> 0xA; Body: 1111 -> 0xF
    bits = bytes([0x0A, 0x0F])
    payload = (
        struct.pack("<iI", names_count, names_length)
        + names_blob
        + bits
    )
    tracks = _parse_visibility_section(payload, 0, frames_count)
    assert len(tracks) == 2
    assert tracks[0].name == "Ball"
    assert tracks[0].values == [False, True, False, True]
    assert tracks[1].values == [True, True, True, True]


def test_mesh_bind_visibility_hides_vco_opt_mesh():
    defaults = mesh_bind_visibility(
        ["BodyAVco_OptMesh", "pm0001_00_BodyBSkin"],
        [],
        opt_mesh_materials={"BodyAVco_OptMesh": ["BodyAVco"]},
    )
    assert defaults["BodyAVco_OptMesh"] is False
    assert defaults["pm0001_00_BodyBSkin"] is True


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_magearna_export_hides_ball_at_bind():
    header = read_garc_slot(ROM, "/a/0/9/4", 0)
    table = parse_model_header_table(header)
    species = 801
    base_group, _, _ = table[species - 1]
    desc = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": species,
        "form": 0,
        "name": "Magearna",
        "type": "model",
    }
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        glb_path = build_model_glb(desc, Path(td))
        raw = glb_path.read_bytes()
        jlen = struct.unpack_from("<I", raw, 12)[0]
        doc = __import__("json").loads(raw[20 : 20 + jlen].decode())
        ball_node = next(
            n
            for n in doc["nodes"]
            if n.get("name") == "pm0882_11_BallASkin" and "mesh" in n
        )
        assert ball_node["extras"]["rae"]["defaultVisible"] is False
        anim = next(a for a in doc["animations"] if a["name"] == "slot4_00")
        assert "pm0882_11_BallASkin" in anim["extras"]["rae"]["meshVisibility"]


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_victini_export_leye_has_expression_sheet():
    header = read_garc_slot(ROM, "/a/0/9/4", 0)
    table = parse_model_header_table(header)
    species = 494
    base_group, _, _ = table[species - 1]
    desc = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": species,
        "form": 0,
        "name": "Victini",
        "type": "model",
    }
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        glb_path = build_model_glb(desc, Path(td))
        raw = glb_path.read_bytes()
        jlen = struct.unpack_from("<I", raw, 12)[0]
        doc = __import__("json").loads(raw[20 : 20 + jlen].decode())
        leye = next(m for m in doc["materials"] if m["name"] == "LEye")
        assert leye["extras"]["rae"]["materialRole"] == "eye_sclera"
        assert leye["extras"]["rae"]["eyeExpression"]["frameCount"] == 8
        vco = next(
            n for n in doc["nodes"] if n.get("name") == "BodyAVco_OptMesh" and "mesh" in n
        )
        assert vco["extras"]["rae"]["defaultVisible"] is False


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_reshiram_eye_mesh_draws_after_accents():
    header = read_garc_slot(ROM, "/a/0/9/4", 0)
    table = parse_model_header_table(header)
    species = 643
    base_group, _, _ = table[species - 1]
    desc = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": species,
        "form": 0,
        "name": "Reshiram",
        "type": "model",
    }
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        glb_path = build_model_glb(desc, Path(td))
        raw = glb_path.read_bytes()
        jlen = struct.unpack_from("<I", raw, 12)[0]
        doc = __import__("json").loads(raw[20 : 20 + jlen].decode())
        mesh_nodes = [
            doc["nodes"][i]
            for i in doc["scenes"][0]["nodes"]
            if "mesh" in doc["nodes"][i]
        ]
        assert mesh_nodes[-1]["name"] == "Eye_OptMesh"
        eye_extras = mesh_nodes[-1].get("extras", {}).get("rae", {})
        assert eye_extras.get("renderOrder") == 2
        eye_index = next(
            i for i, n in enumerate(doc["nodes"]) if n.get("name") == "Eye_OptMesh" and "mesh" in n
        )
        later_inc = [
            i
            for i, n in enumerate(doc["nodes"])
            if "mesh" in n and "Inc" in n.get("name", "") and i > eye_index
        ]
        assert later_inc == []
