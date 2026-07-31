"""Generic named-RomFS coverage for the isolated Nintendo 3DS platform."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rae.platforms.threeds.cgfx_policy import apply_cgfx_glb_policy
from rae.platforms.threeds.container import RomFsFile
from rae.platforms.threeds.game_catalog import identify_threeds_game
from rae.platforms.threeds.gltf.glb_io import GlbData, read_glb_json
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
