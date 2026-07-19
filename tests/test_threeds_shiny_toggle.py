"""3DS shiny texture variants embedded in a single GLB."""
from __future__ import annotations

import json
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from rae.platforms.threeds.gf import (
    GfMaterial,
    GfMesh,
    GfModel,
    GfBone,
    GfSubMesh,
    GfTexture,
    GfTextureUnit,
)
from rae.platforms.threeds.glb import write_model_glb
from rae.platforms.threeds.glb import FormVariantExport
from rae.platforms.threeds.motion import GfMotBoneTrack, GfMotKey, GfMotion
from rae.platforms.threeds.export_module import ThreedsExportModule
from rae.platforms.threeds.model_module import ThreedsModelModule
from rae.scanner import Asset


def _gfmd_asset() -> Asset:
    payload = (
        b'{"type":"model","rom":"roms/test.cci","garc":"/a/0/9/4",'
        b'"group":1,"base_slot":10,"species":54,"form":0,"name":"Psyduck"}'
    )
    return Asset(
        asset_id="gfmd1",
        virtual_path="3ds/test/pokemon/0054 Psyduck/form_00/model.gfmodel",
        kind="3DS Pokémon model",
        magic="GFMD",
        extension=".gfmodel",
        data=payload,
        original_data=payload,
    )


def _read_glb_json(path: Path) -> dict:
    data = path.read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20 : 20 + json_length])


def _read_glb(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    json_length = struct.unpack_from("<I", data, 12)[0]
    doc = json.loads(data[20 : 20 + json_length])
    bin_offset = 20 + json_length + 8
    return doc, data[bin_offset:]


def _material_texture_index(material: dict) -> int:
    return int(material["pbrMetallicRoughness"]["baseColorTexture"]["index"])


def _image_bytes(doc: dict, bin_blob: bytes, texture_index: int) -> bytes:
    texture = doc["textures"][texture_index]
    image = doc["images"][texture["source"]]
    view = doc["bufferViews"][image["bufferView"]]
    offset = int(view.get("byteOffset", 0))
    length = int(view["byteLength"])
    return bin_blob[offset : offset + length]


def test_writer_embeds_distinct_shiny_texture_bytes(monkeypatch, tmp_path: Path) -> None:
    def fake_decode(self: GfTexture) -> bytes:
        return self.raw

    monkeypatch.setattr(GfTexture, "decode_rgba", fake_decode)

    material = GfMaterial(
        name="Body",
        texture_names=["BodyAlb"],
        texture_units=[GfTextureUnit(name="BodyAlb", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0))],
    )
    mesh = GfMesh(
        name="Body",
        submeshes=[
            GfSubMesh(
                material_name="Body",
                positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
                normals=[(0.0, 0.0, 1.0)] * 3,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                indices=[0, 1, 2],
            )
        ],
    )
    model = GfModel(
        name="variant_test",
        texture_names=["BodyAlb"],
        material_names=["Body"],
        materials=[material],
        meshes=[mesh],
    )
    normal = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((255, 0, 0, 255)))
    shiny = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((0, 0, 255, 255)))

    glb = write_model_glb(model, [normal], tmp_path / "variant_test.glb", shiny_textures=[shiny])
    doc, bin_blob = _read_glb(glb)
    materials = doc["materials"]
    shiny_index = materials[0]["extras"]["rae"]["shinyMaterialIndex"]
    normal_texture = _material_texture_index(materials[0])
    shiny_texture = _material_texture_index(materials[shiny_index])

    assert normal_texture != shiny_texture
    assert _image_bytes(doc, bin_blob, normal_texture) != _image_bytes(doc, bin_blob, shiny_texture)
    appearance = doc.get("extras", {}).get("rae", {}).get("appearanceVariants")
    assert appearance["axes"][0]["id"] == "texture"
    assert [option["id"] for option in appearance["axes"][0]["options"]] == ["normal", "shiny"]


def _single_triangle_model(name: str, *, material_name: str = "Body", size: float = 1.0) -> GfModel:
    material = GfMaterial(
        name=material_name,
        texture_names=["BodyAlb"],
        texture_units=[GfTextureUnit(name="BodyAlb", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0))],
    )
    mesh = GfMesh(
        name="Body",
        submeshes=[
            GfSubMesh(
                material_name=material_name,
                positions=[(0.0, 0.0, 0.0), (size, 0.0, 0.0), (0.0, size, 0.0)],
                normals=[(0.0, 0.0, 1.0)] * 3,
                uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
                indices=[0, 1, 2],
            )
        ],
    )
    return GfModel(
        name=name,
        texture_names=["BodyAlb"],
        material_names=[material_name],
        materials=[material],
        meshes=[mesh],
    )


def _skinned_triangle_model(name: str, *, size: float = 1.0) -> GfModel:
    model = _single_triangle_model(name, size=size)
    model.bones = [
        GfBone(
            name="Root",
            parent="",
            flags=0,
            scale=(1.0, 1.0, 1.0),
            rotation=(0.0, 0.0, 0.0),
            translation=(0.0, 0.0, 0.0),
        )
    ]
    sub = model.meshes[0].submeshes[0]
    sub.bone_table = [0]
    sub.joints = [(0, 0, 0, 0)] * 3
    sub.weights = [(1.0, 0.0, 0.0, 0.0)] * 3
    return model


def _root_translate_motion(name: str) -> GfMotion:
    track = GfMotBoneTrack(name="Root", is_axis_angle=False)
    track.channels[7] = [
        GfMotKey(0, 0.0, 0.0),
        GfMotKey(30, 1.0, 0.0),
    ]
    return GfMotion(
        name=name,
        frames_count=30,
        is_looping=True,
        bones=[track],
    )


def test_writer_exports_form_axis_material_and_geometry_metadata(monkeypatch, tmp_path: Path) -> None:
    def fake_decode(self: GfTexture) -> bytes:
        return self.raw

    monkeypatch.setattr(GfTexture, "decode_rgba", fake_decode)

    base = _single_triangle_model("base")
    texture_form = _single_triangle_model("pattern")
    geometry_form = _single_triangle_model("origin", size=2.0)
    normal = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((255, 0, 0, 255)))
    pattern = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((0, 255, 0, 255)))
    origin = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((0, 0, 255, 255)))

    glb = write_model_glb(
        base,
        [normal],
        tmp_path / "forms.glb",
        default_form_variant="00",
        form_variants=[
            FormVariantExport("01", "Pattern", texture_form, [pattern], geometry="texture_only"),
            FormVariantExport("02", "Origin", geometry_form, [origin], geometry="full_geometry"),
        ],
    )
    doc = _read_glb_json(glb)
    rae = doc.get("extras", {}).get("rae", {})
    appearance = rae.get("appearanceVariants")
    assert appearance["default"]["form"] == "00"
    form_axis = next(axis for axis in appearance["axes"] if axis["id"] == "form")
    assert [option["id"] for option in form_axis["options"]] == ["00", "01", "02"]
    base_material = doc["materials"][0]
    assert base_material["extras"]["rae"]["formMaterialIndices"]["01"] > 0
    mesh_nodes = [node for node in doc["nodes"] if "mesh" in node]
    assert any(
        node.get("extras", {}).get("rae", {}).get("visibleForForms") == ["00", "01"]
        for node in mesh_nodes
    )
    assert any(
        node.get("extras", {}).get("rae", {}).get("visibleForForms") == ["02"]
        for node in mesh_nodes
    )


def test_writer_skins_compatible_full_geometry_form(monkeypatch, tmp_path: Path) -> None:
    def fake_decode(self: GfTexture) -> bytes:
        return self.raw

    monkeypatch.setattr(GfTexture, "decode_rgba", fake_decode)

    base = _skinned_triangle_model("base")
    alternate = _skinned_triangle_model("alternate", size=2.0)
    normal = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((255, 0, 0, 255)))
    alt_tex = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((0, 0, 255, 255)))
    glb = write_model_glb(
        base,
        [normal],
        tmp_path / "skinned_forms.glb",
        animations=[_root_translate_motion("slot4_00")],
        default_form_variant="00",
        form_variants=[
            FormVariantExport(
                "01",
                "Alt",
                alternate,
                [alt_tex],
                animations=[_root_translate_motion("different_form_idle_name")],
                geometry="full_geometry",
            ),
        ],
    )
    doc = _read_glb_json(glb)
    alt_nodes = [
        node
        for node in doc["nodes"]
        if node.get("extras", {}).get("rae", {}).get("visibleForForms") == ["01"]
    ]
    assert alt_nodes
    assert len(doc.get("skins", [])) == 2
    assert all(node.get("skin") == 1 for node in alt_nodes)
    alt_mesh = doc["meshes"][alt_nodes[0]["mesh"]]
    attrs = alt_mesh["primitives"][0]["attributes"]
    assert "JOINTS_0" in attrs
    assert "WEIGHTS_0" in attrs
    alt_bone_nodes = [
        i
        for i, node in enumerate(doc["nodes"])
        if node.get("name") == "Root__form_01"
    ]
    assert alt_bone_nodes
    assert any(
        channel["target"]["node"] == alt_bone_nodes[0]
        for animation in doc.get("animations", [])
        if animation.get("name") == "slot4_00"
        for channel in animation.get("channels", [])
    )


def test_writer_retargets_base_animation_to_full_geometry_form_without_form_motion(
    monkeypatch,
    tmp_path: Path,
) -> None:
    def fake_decode(self: GfTexture) -> bytes:
        return self.raw

    monkeypatch.setattr(GfTexture, "decode_rgba", fake_decode)

    base = _skinned_triangle_model("base")
    alternate = _skinned_triangle_model("alternate", size=2.0)
    normal = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((255, 0, 0, 255)))
    alt_tex = GfTexture("BodyAlb", 1, 1, 0x04, 0, bytes((0, 0, 255, 255)))
    glb = write_model_glb(
        base,
        [normal],
        tmp_path / "retargeted_forms.glb",
        animations=[_root_translate_motion("slot4_00")],
        default_form_variant="00",
        form_variants=[
            FormVariantExport(
                "01",
                "Alt",
                alternate,
                [alt_tex],
                animations=[],
                geometry="full_geometry",
            ),
        ],
    )
    doc = _read_glb_json(glb)
    base_root = next(i for i, node in enumerate(doc["nodes"]) if node.get("name") == "Root")
    alt_root = next(i for i, node in enumerate(doc["nodes"]) if node.get("name") == "Root__form_01")
    slot4 = next(animation for animation in doc.get("animations", []) if animation.get("name") == "slot4_00")
    targets = [channel["target"]["node"] for channel in slot4.get("channels", [])]

    assert base_root in targets
    assert alt_root in targets


def test_export_glb_single_file_with_default_variant(monkeypatch, tmp_path: Path) -> None:
    calls: list[bool] = []

    def fake_build(descriptor, out_dir, *, shiny=False, progress=None):
        calls.append(shiny)
        path = Path(out_dir) / "pm0054_00_Psyduck.glb"
        path.write_bytes(b"glTF")
        return path

    monkeypatch.setattr(
        "rae.platforms.threeds.export_module.build_model_glb",
        fake_build,
    )
    monkeypatch.setattr(
        "rae.platforms.threeds.export_module.load_descriptor",
        lambda asset: {"type": "model", "base_slot": 10, "species": 54},
    )

    module = ThreedsExportModule()
    host = SimpleNamespace(_export_glb_shiny=True)
    written = module.run_export_choice(host, _gfmd_asset(), "glb", tmp_path)
    assert len(written) == 1
    assert written[0].name == "pm0054_00_Psyduck.glb"
    assert calls == [True]


def test_export_options_offer_glb_then_glbz() -> None:
    module = ThreedsExportModule()
    keys = [key for key, _, _ in module.export_options_for(_gfmd_asset())]
    assert keys[:2] == ["glb", "glbz"]
    assert "glb_shiny" not in keys


def test_glbz_export_uses_glb_pipeline_then_compresses(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_build(descriptor, out_dir, *, shiny=False, progress=None):
        del descriptor, progress
        path = Path(out_dir) / "pm0054_00_Psyduck.glb"
        path.write_bytes(b"glTF temporary")
        calls.append((path.suffix, shiny))
        return path

    def fake_write(source, destination):
        assert Path(source).read_bytes() == b"glTF temporary"
        Path(destination).write_bytes(b"PRGLBZ01 compiled")
        return Path(destination)

    monkeypatch.setattr("rae.platforms.threeds.export_module.build_model_glb", fake_build)
    monkeypatch.setattr("rae.platforms.threeds.export_module.write_glbz", fake_write)
    monkeypatch.setattr(
        "rae.platforms.threeds.export_module.load_descriptor",
        lambda asset: {"type": "model", "base_slot": 10, "species": 54},
    )

    module = ThreedsExportModule()
    host = SimpleNamespace(_export_glb_shiny=True)
    written = module.run_export_choice(host, _gfmd_asset(), "glbz", tmp_path)
    assert written == [tmp_path / "pm0054_00_Psyduck.glbz"]
    assert calls == [(".glb", True)]


def test_model_module_preview_shiny_uses_viewer_variant() -> None:
    calls: list[str] = []

    class Preview:
        def set_texture_variant(self, variant_id: str) -> None:
            calls.append(variant_id)

    window = SimpleNamespace(
        _threeds_preview_asset=_gfmd_asset(),
        preview=Preview(),
    )
    module = ThreedsModelModule()
    assert module.set_preview_shiny(window, shiny=True)
    assert calls == ["shiny"]
    assert window._threeds_preview_shiny is True


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_pokemon_glb_embeds_shiny_material_siblings(tmp_path: Path) -> None:
    from rae.platforms.threeds.rom import load_descriptor, scan_threeds_rom_path
    from rae.platforms.threeds.service import build_model_glb

    rom = Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"
    assets = scan_threeds_rom_path(str(rom), progress=lambda m: None)
    psyduck = next(a for a in assets if "0054 Psyduck" in a.virtual_path)
    descriptor = load_descriptor(psyduck)
    glb = build_model_glb(descriptor, tmp_path)
    doc = _read_glb_json(glb)
    materials = doc.get("materials") or []
    with_shiny = [
        m
        for m in materials
        if isinstance(m.get("extras"), dict)
        and isinstance(m["extras"].get("rae"), dict)
        and "shinyMaterialIndex" in m["extras"]["rae"]
    ]
    assert with_shiny, "expected normal materials to reference shiny siblings"
    texture_pairs = []
    for material in with_shiny:
        shiny_index = material["extras"]["rae"]["shinyMaterialIndex"]
        normal_pbr = material.get("pbrMetallicRoughness") or {}
        shiny_pbr = materials[shiny_index].get("pbrMetallicRoughness") or {}
        if "baseColorTexture" in normal_pbr and "baseColorTexture" in shiny_pbr:
            texture_pairs.append(
                (
                    normal_pbr["baseColorTexture"]["index"],
                    shiny_pbr["baseColorTexture"]["index"],
                )
            )
    assert any(normal != shiny for normal, shiny in texture_pairs)
    variants = doc.get("extras", {}).get("rae", {}).get("textureVariants")
    assert variants and variants.get("options")
    appearance = doc.get("extras", {}).get("rae", {}).get("appearanceVariants")
    assert appearance and appearance.get("axes")
    assert len(materials) > len(with_shiny)
