from __future__ import annotations

from pathlib import Path

from rae.platforms.unity.mesh_preview import _parse_obj, _split_obj_by_groups, _write_glb


def test_parse_obj_preserves_uv_coordinates():
    obj_text = """\
v 0 0 0
v 1 0 0
v 0 1 0
vt 0 0
vt 1 0
vt 0 1
f 1/1/1 2/2/2 3/3/3
"""
    model = _parse_obj(obj_text)
    assert model.has_uvs
    assert len(model.positions) == 3
    assert len(model.uvs) == 3
    assert model.faces == [(0, 1, 2)]


def test_write_glb_includes_texcoords(tmp_path):
    obj_text = Path("/tmp/psyduck_obj/assets/sandbox/models/01rg/pm0054_00_00/fbx/model/pm0054_00_00_BodySkin.obj")
    if not obj_text.is_file():
        import pytest

        pytest.skip("local AssetStudio OBJ export not available")
    model = _parse_obj(obj_text.read_text(encoding="utf-8", errors="replace"))
    assert model.has_uvs
    out = tmp_path / "test.glb"
    _write_glb(model, out, mesh_name="BodySkin")
    import json
    import struct

    data = out.read_bytes()
    off = 12
    while off < len(data):
        chunk_len, chunk_type = struct.unpack_from("<I4s", data, off)
        if chunk_type == b"JSON":
            gltf = json.loads(data[off + 8 : off + 8 + chunk_len])
            attrs = gltf["meshes"][0]["primitives"][0]["attributes"]
            assert "TEXCOORD_0" in attrs
            break
        off += 8 + chunk_len
    else:
        raise AssertionError("JSON chunk not found")


def test_parse_obj_splits_assetstudio_groups():
    obj_text = """\
v 0 0 0
v 1 0 0
v 0 1 0
v 1 1 0
vt 0 0
vt 1 0
vt 0 1
vt 1 1
g part_a
f 1/1/1 2/2/2 3/3/3
g part_b
f 2/2/2 4/4/4 3/3/3
"""
    model = _parse_obj(obj_text)
    parts = _split_obj_by_groups(model)
    assert len(parts) == 2
    assert parts[0][0] == "part_a"
    assert parts[1][0] == "part_b"
    assert len(parts[0][1].faces) == 1
    assert len(parts[1][1].faces) == 1


def test_write_glb_grouped_exports_named_materials(tmp_path):
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
    out = tmp_path / "grouped.glb"
    _write_glb(model, out, mesh_name="BodySkin")
    import json
    import struct

    data = out.read_bytes()
    off = 12
    while off < len(data):
        chunk_len, chunk_type = struct.unpack_from("<I4s", data, off)
        if chunk_type == b"JSON":
            gltf = json.loads(data[off + 8 : off + 8 + chunk_len])
            assert len(gltf["meshes"][0]["primitives"]) == 2
            assert gltf["materials"][0]["name"] == "BodySkin_0"
            assert gltf["materials"][1]["name"] == "BodySkin_3"
            assert "images" not in gltf or not gltf["images"]
            break
        off += 8 + chunk_len
    else:
        raise AssertionError("JSON chunk not found")
