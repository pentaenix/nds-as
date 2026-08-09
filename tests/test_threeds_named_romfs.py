"""Generic named-RomFS coverage for the isolated Nintendo 3DS platform."""
from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace
from xml.etree import ElementTree as ET

import pytest

from rae.platforms.threeds.cgfx_policy import apply_cgfx_dae_policy, apply_cgfx_glb_policy
from rae.platforms.threeds.container import RomFsFile
from rae.platforms.threeds.game_catalog import identify_threeds_game
from rae.platforms.threeds.gltf.glb_io import GlbData, read_glb_json
from rae.platforms.threeds.lbx_catalog import LbxCatalog
from rae.platforms.threeds.named_romfs import scan_named_romfs_assets

pytestmark = pytest.mark.threeds

LBX_ROM = (
    Path(__file__).resolve().parent.parent
    / "roms"
    / "LBX - Little Battlers eXperience (USA) (En,Fr,Es).cci"
)


class _HeaderImage:
    def __init__(self, payloads: dict[int, bytes]):
        self.payloads = payloads

    def read(self, offset: int, size: int) -> bytes:
        return self.payloads[offset][:size]


def test_game_profiles_keep_ultra_moon_separate_from_named_romfs_games():
    assert identify_threeds_game("CTR-P-A2BE") == "pokemon_ultra_moon"
    assert identify_threeds_game("A2BP") == "pokemon_ultra_moon"
    assert identify_threeds_game("CTR-P-ADNE") == "lbx"
    assert (
        identify_threeds_game(
            "CTR-P-ZZZZ",
            romfs_paths={"/3ddata/model/example.bcmdl", "/3ddata/animation/example.bcskla"},
        )
        == "lbx"
    )
    assert identify_threeds_game("CTR-P-ZZZZ", romfs_paths={"/model/example.bcmdl"}) == "generic"


def test_named_romfs_scanner_checks_extension_and_cgfx_header():
    files = [
        RomFsFile("/models/Hero.BCMDL", 10, 8),
        RomFsFile("/animations/Hero_idle.BCSKLA", 20, 8),
        RomFsFile("/models/not_really.bcmdl", 30, 8),
        RomFsFile("/notes/readme.txt", 40, 8),
    ]
    image = _HeaderImage(
        {10: b"CGFXmdl!", 20: b"CGFXanim", 30: b"NOPEdata", 40: b"CGFXtext"}
    )
    reports: list[str] = []
    assets = scan_named_romfs_assets(
        image,  # type: ignore[arg-type]
        files,
        rom_path="/tmp/example.cci",
        rom_stem="example",
        product_code="CTR-P-ZZZZ",
        game_id="generic",
        report=reports.append,
    )

    assert [asset.magic for asset in assets] == ["CGSA", "CGMD"]
    descriptors = [json.loads(asset.data) for asset in assets]
    assert {item["romfs_path"] for item in descriptors} == {
        "/animations/Hero_idle.BCSKLA",
        "/models/Hero.BCMDL",
    }
    assert all(item["game"] == "generic" for item in descriptors)
    assert reports and "2 rows" in reports[-1]


def test_scan_dispatch_does_not_send_ultra_moon_to_generic_scanner(monkeypatch, tmp_path: Path):
    from rae.platforms.threeds import rom as rom_module

    calls: list[str] = []

    class FakeImage:
        def __init__(self, _path):
            self.part = SimpleNamespace(product_code="CTR-P-A2BE", encrypted=False)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def main_partition(self):
            return self.part

        def romfs_files(self, _part=None):
            return []

    monkeypatch.setattr(rom_module, "ThreedsImage", FakeImage)
    monkeypatch.setattr(rom_module, "_scan_pokemon_models", lambda *_args: calls.append("models") or [])
    monkeypatch.setattr(rom_module, "_scan_pokemon_icons", lambda *_args: calls.append("icons") or [])
    monkeypatch.setattr(rom_module, "_scan_world_garcs", lambda *_args: calls.append("world") or [])
    monkeypatch.setattr(
        rom_module,
        "scan_named_romfs_assets",
        lambda *_args, **_kwargs: calls.append("generic") or [],
    )

    assets = rom_module.scan_threeds_rom_path(tmp_path / "ultra_moon.cci")

    assert len(assets) == 1 and assets[0].magic == "3DSR"
    assert calls == ["models", "icons", "world"]


def test_scan_dispatch_uses_generic_scanner_for_unknown_games(monkeypatch, tmp_path: Path):
    from rae.platforms.threeds import rom as rom_module

    calls: list[str] = []

    class FakeImage:
        def __init__(self, _path):
            self.part = SimpleNamespace(product_code="CTR-P-ZZZZ", encrypted=False)

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return None

        def main_partition(self):
            return self.part

        def romfs_files(self, _part=None):
            return []

    monkeypatch.setattr(rom_module, "ThreedsImage", FakeImage)
    monkeypatch.setattr(
        rom_module,
        "scan_named_romfs_assets",
        lambda *_args, **_kwargs: calls.append("generic") or [],
    )
    monkeypatch.setattr(rom_module, "_scan_pokemon_models", lambda *_args: calls.append("models") or [])

    rom_module.scan_threeds_rom_path(tmp_path / "unknown.cci")

    assert calls == ["generic"]


def test_cgfx_policy_restores_authored_animation_name(tmp_path: Path):
    path = tmp_path / "model.glb"
    GlbData(
        json={"asset": {"version": "2.0"}, "animations": [{"name": "channel_name"}]},
        bin_chunk=b"",
    ).write(path)

    apply_cgfx_glb_policy(path, game_id="lbx", animation_name="chr001_00_idle_00")

    root = read_glb_json(path)
    assert root["animations"][0]["name"] == "chr001_00_idle_00"
    assert root["extras"]["rae"] == {
        "platform": "3ds",
        "schemaVersion": 1,
        "game": "lbx",
        "format": "cgfx",
    }


def test_cgfx_policy_preserves_authoritative_culling_and_additive_blend(tmp_path: Path):
    path = tmp_path / "model.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "line_M"}, {"name": "glow_M"}],
        },
        bin_chunk=b"",
    ).write(path)
    states = [
        {
            "name": "line_M",
            "faceCulling": "BackFace",
            "alphaTestEnabled": False,
            "colorSourceFactor": "One",
            "colorDestinationFactor": "Zero",
            "depthTestEnabled": True,
            "depthWriteEnabled": True,
        },
        {
            "name": "glow_M",
            "faceCulling": "Never",
            "alphaTestEnabled": False,
            "colorSourceFactor": "SourceAlpha",
            "colorDestinationFactor": "One",
            "depthTestEnabled": True,
            "depthWriteEnabled": False,
        },
    ]

    apply_cgfx_glb_policy(path, game_id="lbx", material_states=states)

    materials = read_glb_json(path)["materials"]
    assert materials[0]["doubleSided"] is False
    assert materials[0]["extras"]["rae"]["pica"]["faceCulling"] == "back_face"
    assert materials[0]["pbrMetallicRoughness"]["baseColorFactor"] == [0.04, 0.04, 0.04, 1.0]
    assert materials[0]["extras"]["rae"]["lbxOutline"] is True
    assert materials[1]["doubleSided"] is True
    assert materials[1]["alphaMode"] == "BLEND"
    assert materials[1]["extras"]["rae"]["renderClass"] == "additive"


def test_cgfx_dae_policy_makes_lbx_line_material_flat_dark(tmp_path: Path):
    path = tmp_path / "model.dae"
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_materials>
    <material id="body_id" name="body"><instance_effect url="#body_fx"/></material>
    <material id="line_M_id" name="line_M"><instance_effect url="#line_fx"/></material>
  </library_materials>
  <library_effects>
    <effect id="body_fx"><profile_COMMON><technique><phong><diffuse><texture texture="body"/></diffuse></phong></technique></profile_COMMON></effect>
    <effect id="line_fx"><profile_COMMON><technique><phong><diffuse><texture texture="body"/></diffuse></phong></technique></profile_COMMON></effect>
  </library_effects>
</COLLADA>""",
        encoding="utf-8",
    )

    apply_cgfx_dae_policy(path, game_id="lbx")

    root = ET.parse(path).getroot()
    effects = {effect.get("id"): effect for effect in root.findall(".//{*}effect")}
    assert effects["body_fx"].find(".//{*}diffuse/{*}texture") is not None
    assert effects["line_fx"].findtext(".//{*}diffuse/{*}color") == "0.04 0.04 0.04 1"


def test_cgfx_dae_policy_marks_authored_vertex_alpha_blend(tmp_path: Path):
    path = tmp_path / "shadow.dae"
    path.write_text(
        """<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
<library_materials><material id="shadow_id" name="TR_shadow_M"><instance_effect url="#shadow_fx"/></material></library_materials>
<library_effects><effect id="shadow_fx"><profile_COMMON><technique><phong><diffuse><color>1 1 1 1</color></diffuse></phong></technique></profile_COMMON></effect></library_effects>
</COLLADA>""",
        encoding="utf-8",
    )
    apply_cgfx_dae_policy(
        path,
        game_id="lbx",
        material_states=[{
            "name": "TR_shadow_M",
            "colorSourceFactor": "SourceAlpha",
            "colorDestinationFactor": "OneMinusSourceAlpha",
        }],
    )
    root = ET.parse(path).getroot()
    assert root.find(".//{*}effect[@id='shadow_fx']//{*}transparent") is not None
    assert root.findtext(".//{*}effect[@id='shadow_fx']//{*}transparency/{*}float") == "1"


@pytest.mark.skipif(not LBX_ROM.is_file(), reason="LBX test ROM not present")
def test_lbx_real_rom_catalog_has_models_textures_and_animations():
    from rae.platforms.threeds.rom import load_descriptor, scan_threeds_rom_path

    assets = scan_threeds_rom_path(LBX_ROM)
    counts = {
        magic: sum(asset.magic == magic for asset in assets)
        for magic in ("CGMD", "CGTX", "CGSA", "CGMA", "CGCA")
    }
    assert counts["CGMD"] > 2_000
    assert counts["CGTX"] > 1_000
    assert counts["CGSA"] > 5_000
    assert counts["CGMA"] > 100
    assert counts["CGCA"] > 100
    character = next(asset for asset in assets if asset.virtual_path.endswith("/chr001_00.bcmdl"))
    descriptor = load_descriptor(character)
    assert descriptor is not None
    assert descriptor["game"] == "lbx"
    assert descriptor["romfs_path"] == "/3ddata/model/chr001_00.bcmdl"


@pytest.mark.skipif(not LBX_ROM.is_file(), reason="LBX test ROM not present")
def test_lbx_models_resource_plan_names_and_groups_requested_assets():
    jobs = LbxCatalog(LBX_ROM).build_jobs()
    by_path = {job.romfs_path: job for job in jobs}

    assert by_path["/3ddata/coreparts/itm_cop_battery07_00.bcmdl"].relative_directory == Path(
        "LBX/Chips/Batteries/Battery 07"
    )
    assert by_path["/3ddata/parts/model/lbx011_01_head.bcmdl"].title.startswith(
        "Chameleon Head"
    )
    assert by_path["/3ddata/parts/model/lbx001_01_body.bcmdl"].title == "DK Achilles Body"
    assert by_path["/3ddata/parts/model/lbx111_01_body.bcmdl"].title == (
        "Destroyer G-Lex Body"
    )
    assert by_path["/3ddata/parts/model/lbx118_00_body.bcmdl"].title == "Genbu Body"
    assert by_path["/3ddata/parts/model/lbx211_00_body.bcmdl"].title == "Blue Ribbon Body"
    assert by_path["/3ddata/parts/model/lbx038_00_body.bcmdl"].title == "Destroyer Z Body"
    assert by_path["/3ddata/parts/model/lbx001_00_body.bcmdl"].custom_r_path == (
        "/3ddata/parts/custom_r/lbx001_00_body_r.bcmdl"
    )
    assert not any(job.romfs_path.startswith("/3ddata/parts/custom_r/") for job in jobs)
    assert by_path["/3ddata/parts/model/lbx000_sepia_01_arm_L.bcmdl"].title == (
        "Cover Pads Left Arm Sepia"
    )
    assert by_path["/3ddata/wpn/wpn_gu_ha04_00.bcmdl"].relative_directory == Path(
        "LBX/Weapons/Guns/Handgun 04"
    )
    assert by_path["/3ddata/wpn/wpn_sh_sq05_01.bcmdl"].title == "Tower Shield B 05"
    assert by_path["/3ddata/coreparts/itm_cop_battery02_00.bcmdl"].title == (
        "Battery A 02"
    )
    assert by_path["/3ddata/parts/model/lbx120_00_leg.bcmdl"].auxiliary_paths == (
        "/3ddata/parts/model/lbx120_00_leg_tr.bcmdl",
    )
    assert not any(
        job.romfs_path.startswith("/3ddata/coreparts/")
        and re.search(r"\d{2}_\d{2}_l$", Path(job.romfs_path).stem.casefold())
        for job in jobs
    )
    assert "/3ddata/wpn/wpn_fi_fi_sude.bcmdl" not in by_path
