"""3DS GF material render-state and lossless GLBZ regressions."""
from __future__ import annotations

from pathlib import Path
import struct

import pytest

from rae.platforms.threeds.glbz import decode_glbz, encode_glbz, write_glbz
from rae.platforms.threeds.gltf.glb_io import read_glb
from rae.platforms.threeds.pica_material import parse_pica_render_state
from rae.platforms.threeds.rom import MODEL_GROUP_STRIDE, parse_model_header_table, read_garc_slot
from rae.platforms.threeds.service import build_model_glb

pytestmark = pytest.mark.threeds
ROM = Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"


def _command(parameter: int, register: int, mask: int) -> bytes:
    return struct.pack("<II", parameter, register | (mask << 16))


def test_parse_pica_source_alpha_render_state() -> None:
    commands = b"".join(
        (
            _command(2, 0x0040, 0xF),
            _command(0x00E40100, 0x0100, 0x3),
            _command(0x76760000, 0x0101, 0xF),
            _command(0x00000061, 0x0104, 0x3),
            _command(0x00000F51, 0x0107, 0xF),
        )
    )
    state = parse_pica_render_state(b"variable metadata" + commands)
    assert state is not None
    assert state.render_class() == "blend"
    assert state.alpha_test_enabled
    assert state.alpha_test_function == 6
    assert not state.depth_write_enabled
    assert state.to_extras()["sourceRgbFactor"] == "source_alpha"
    assert state.cull_backface_enabled
    assert not state.cull_frontface_enabled
    assert state.to_extras()["cullBackface"] is True


def test_glbz_round_trip_is_lossless(tmp_path: Path) -> None:
    json_chunk = b'{}  '
    source = struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(json_chunk))
    source += struct.pack("<II", len(json_chunk), 0x4E4F534A) + json_chunk
    container = encode_glbz(source)
    assert decode_glbz(container) == source

    glb_path = tmp_path / "model.glb"
    glb_path.write_bytes(source)
    output = write_glbz(glb_path, tmp_path / "model.glbz")
    assert output.suffix == ".glbz"
    assert decode_glbz(output.read_bytes()) == source


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_dewpider_uses_source_render_state_and_preserves_vco_features(tmp_path: Path) -> None:
    table = parse_model_header_table(read_garc_slot(ROM, "/a/0/9/4", 0))
    base_group, _, _ = table[751 - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": 751,
        "form": 0,
        "name": "Dewpider",
        "type": "model",
    }
    document = read_glb(build_model_glb(descriptor, tmp_path)).json
    materials = {material["name"]: material for material in document["materials"]}
    assert materials["BodyUniranNone"]["alphaMode"] == "BLEND"
    assert materials["BodyUniranNone"]["extras"]["rae"]["pica"]["depthWriteEnabled"] is False
    assert materials["BodyUniranNone"]["extras"]["rae"]["nitro"]["cullBackface"] is True
    assert not materials["BodyUniranNone"].get("doubleSided", False)
    assert "alphaMode" not in materials["BodyVco00"]
    assert materials["BodyVco00"]["extras"]["rae"]["renderClass"] == "opaque"

    nodes = {node.get("name"): node for node in document["nodes"] if "mesh" in node}
    assert nodes["BodyUniranVco_OptMesh"]["extras"]["rae"]["defaultVisible"] is False
    for name in ("BodyVco00_OptMesh", "BodyVco01_OptMesh"):
        node = nodes[name]
        assert node["extras"]["rae"]["defaultVisible"] is True
        primitives = document["meshes"][node["mesh"]]["primitives"]
        assert all("COLOR_0" in primitive["attributes"] for primitive in primitives)


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_morelull_preserves_camera_sphere_glow_mapping(tmp_path: Path) -> None:
    table = parse_model_header_table(read_garc_slot(ROM, "/a/0/9/4", 0))
    base_group, _, _ = table[755 - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": 755,
        "form": 0,
        "name": "Morelull",
        "type": "model",
    }
    document = read_glb(build_model_glb(descriptor, tmp_path)).json
    materials = {material["name"]: material for material in document["materials"]}
    glow = materials["GlowInc"]
    assert glow["extras"]["rae"]["textureMapping"] == {
        "type": "camera_sphere_environment",
        "sourceValue": 2,
    }
    assert glow["extras"]["rae"]["renderClass"] == "additive"
    assert not glow.get("doubleSided", False)


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
@pytest.mark.parametrize(
    ("species", "name", "material_name"),
    (
        (791, "Solgaleo", "BodyA00_white"),
        (792, "Lunala", "BodyB"),
    ),
)
def test_solid_pokemon_materials_do_not_export_tev_vertex_alpha(
    tmp_path: Path,
    species: int,
    name: str,
    material_name: str,
) -> None:
    table = parse_model_header_table(read_garc_slot(ROM, "/a/0/9/4", 0))
    base_group, _, _ = table[species - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": species,
        "form": 0,
        "name": name,
        "type": "model",
    }
    document = read_glb(build_model_glb(descriptor, tmp_path)).json
    material_index = next(
        index
        for index, material in enumerate(document["materials"])
        if material["name"] == material_name
    )
    primitives = [
        primitive
        for mesh in document["meshes"]
        for primitive in mesh["primitives"]
        if primitive["material"] == material_index and "COLOR_0" in primitive["attributes"]
    ]
    assert primitives
    assert all(
        document["accessors"][primitive["attributes"]["COLOR_0"]]["type"] == "VEC3"
        for primitive in primitives
    )


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_xurkitree_additive_glow_has_transparent_black_key(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import material_texture_bytes
    from rae.platforms.threeds.gltf.texture_alpha import texture_has_fully_transparent_pixels

    table = parse_model_header_table(read_garc_slot(ROM, "/a/0/9/4", 0))
    base_group, _, _ = table[796 - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": 796,
        "form": 0,
        "name": "Xurkitree",
        "type": "model",
    }
    glb = read_glb(build_model_glb(descriptor, tmp_path))
    glow_indices = [
        index
        for index, material in enumerate(glb.json["materials"])
        if material["name"].startswith("GlowInc")
    ]
    assert glow_indices
    for index in glow_indices:
        material = glb.json["materials"][index]
        assert material["extras"]["rae"]["renderClass"] == "additive"
        assert texture_has_fully_transparent_pixels(material_texture_bytes(glb, index))


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
def test_morelull_caps_are_opaque_and_glow_is_additive(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import material_texture_bytes
    from rae.platforms.threeds.gltf.texture_alpha import texture_has_fully_transparent_pixels

    table = parse_model_header_table(read_garc_slot(ROM, "/a/0/9/4", 0))
    base_group, _, _ = table[755 - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": 755,
        "form": 0,
        "name": "Morelull",
        "type": "model",
    }
    glb = read_glb(build_model_glb(descriptor, tmp_path))
    materials = {material["name"]: (index, material) for index, material in enumerate(glb.json["materials"])}
    for name in ("BodyBInc1", "BodyBInc2"):
        _index, material = materials[name]
        assert material["extras"]["rae"]["renderClass"] == "opaque"
        assert material.get("alphaMode", "OPAQUE") == "OPAQUE"

    glow_index, glow = materials["GlowInc"]
    assert glow["extras"]["rae"]["renderClass"] == "additive"
    assert glow["alphaMode"] == "BLEND"
    assert texture_has_fully_transparent_pixels(material_texture_bytes(glb, glow_index))


@pytest.mark.skipif(not ROM.is_file(), reason="ROM not available")
@pytest.mark.parametrize(
    ("species", "name", "mesh_names"),
    (
        (483, "Dialga", ("BodyBArceusVco00_OptMesh", "BodyBArceusVco04_OptMesh")),
        (659, "Bunnelby", ("BodyVco_OptMesh",)),
    ),
)
def test_distinct_vco_feature_meshes_default_visible(
    tmp_path: Path,
    species: int,
    name: str,
    mesh_names: tuple[str, ...],
) -> None:
    table = parse_model_header_table(read_garc_slot(ROM, "/a/0/9/4", 0))
    base_group, _, _ = table[species - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": species,
        "form": 0,
        "name": name,
        "type": "model",
    }
    document = read_glb(build_model_glb(descriptor, tmp_path)).json
    nodes = {node.get("name"): node for node in document["nodes"] if "mesh" in node}
    for mesh_name in mesh_names:
        assert nodes[mesh_name]["extras"]["rae"]["defaultVisible"] is True
