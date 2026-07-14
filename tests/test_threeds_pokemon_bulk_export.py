"""Tests for Ultra Moon bulk Pokémon export helpers."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rae.platforms.threeds.game_catalog import (
    ULTRA_MOON_PRODUCT_CODES,
    is_ultra_moon_product_code,
    is_ultra_moon_serial,
    parse_product_serial,
)
from rae.platforms.threeds.pokemon_bulk_export import (
    pokemon_bulk_export_assets,
    pokemon_bulk_export_available,
    resolve_product_code,
    run_pokemon_bulk_export,
)
from rae.scanner import Asset


def _gfmd_asset(*, species: int, form: int, asset_id: str, folder: str) -> Asset:
    payload = json.dumps(
        {
            "type": "model",
            "rom": "roms/test.cci",
            "garc": "/a/0/9/4",
            "group": species,
            "base_slot": 10,
            "species": species,
            "form": form,
            "name": f"Species {species}",
        }
    ).encode()
    return Asset(
        asset_id=asset_id,
        virtual_path=f"3ds/test/pokemon/{folder}/form_{form:02d}/model.gfmodel",
        kind="3DS Pokémon model",
        magic="GFMD",
        extension=".gfmodel",
        data=payload,
        original_data=payload,
    )


def test_ultra_moon_product_codes_include_us_cartridge() -> None:
    assert "CTR-P-A2BA" in ULTRA_MOON_PRODUCT_CODES
    assert is_ultra_moon_product_code("ctr-p-a2ba")
    assert is_ultra_moon_product_code("CTR-P-A2BE")  # NTSC-U retail cart
    assert is_ultra_moon_serial(parse_product_serial("CTR-P-A2BE"))
    assert not is_ultra_moon_product_code("CTR-P-A2AA")  # Ultra Sun US
    assert not is_ultra_moon_product_code("CTR-P-BISA")  # Sun US


def test_pokemon_bulk_export_available_uses_scan_product_code() -> None:
    assets = [_gfmd_asset(species=1, form=0, asset_id="a", folder="0001 Bulbasaur")]
    summary = Asset(
        asset_id="summary",
        virtual_path="3ds/test/rae_3ds_rom.json",
        kind="3DS ROM summary",
        magic="3DSR",
        extension=".json",
        data=json.dumps({"product_code": "CTR-P-A2BE"}).encode(),
        original_data=b"",
    )
    assert pokemon_bulk_export_available(
        "roms/missing.cci",
        [*assets, summary],
        product_code="CTR-P-A2BE",
    )
    assert not pokemon_bulk_export_available(
        "roms/missing.cci",
        [*assets, summary],
        product_code="CTR-P-A2AA",
    )
    assert resolve_product_code("roms/missing.cci", [summary]) == "CTR-P-A2BE"


def test_pokemon_bulk_export_assets_dedupes_species_form_zero_only() -> None:
    assets = [
        _gfmd_asset(species=1, form=0, asset_id="a", folder="0001 Bulbasaur"),
        _gfmd_asset(species=1, form=1, asset_id="b", folder="0001 Bulbasaur"),
        _gfmd_asset(species=2, form=0, asset_id="c", folder="0002 Ivysaur"),
        _gfmd_asset(species=0, form=0, asset_id="d", folder="group_0001"),
    ]
    picked = pokemon_bulk_export_assets(assets)
    assert [a.asset_id for a in picked] == ["a", "c"]


def test_pokemon_bulk_export_available_requires_ultra_moon(monkeypatch) -> None:
    assets = [_gfmd_asset(species=1, form=0, asset_id="a", folder="0001 Bulbasaur")]
    assert pokemon_bulk_export_available("roms/test.cci", assets, product_code="CTR-P-A2BE")
    assert not pokemon_bulk_export_available("roms/test.cci", assets, product_code="CTR-P-A2AA")
    monkeypatch.setattr(
        "rae.platforms.threeds.pokemon_bulk_export.read_product_code",
        lambda _path: "CTR-P-A2BE",
    )
    assert pokemon_bulk_export_available("roms/test.cci", assets)
    monkeypatch.setattr(
        "rae.platforms.threeds.pokemon_bulk_export.read_product_code",
        lambda _path: "CTR-P-A2AA",
    )
    assert not pokemon_bulk_export_available("roms/test.cci", assets)


def test_run_pokemon_bulk_export_uses_export_choice(monkeypatch, tmp_path: Path) -> None:
    assets = [
        _gfmd_asset(species=1, form=0, asset_id="a", folder="0001 Bulbasaur"),
        _gfmd_asset(species=2, form=0, asset_id="b", folder="0002 Ivysaur"),
    ]
    calls: list[tuple[str, str]] = []

    def fake_run(host, asset, choice, out):
        calls.append((asset.asset_id, choice))
        path = Path(out) / f"{asset.asset_id}.glb"
        path.write_bytes(b"glTF")
        return [path]

    monkeypatch.setattr(
        "rae.platforms.threeds.pokemon_bulk_export.resolve_product_code",
        lambda *_args, **_kwargs: "CTR-P-A2BE",
    )

    host = SimpleNamespace(_export_glb_shiny=False)
    result = run_pokemon_bulk_export(
        host,
        assets,
        "roms/test.cci",
        tmp_path,
        export_choice=fake_run,
    )
    assert calls == [("a", "glb"), ("b", "glb")]
    assert len(result.written) == 2
    assert result.manifest_path is not None
    assert result.manifest_path.is_file()
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["product_code"] == "CTR-P-A2BE"
    assert manifest["files_written"] == 2
