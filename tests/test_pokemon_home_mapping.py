from __future__ import annotations

import pytest

from rae.platforms.home.ids import parse_home_asset_id, parse_home_cap_id, parse_home_mitake_preview_id, parse_home_pokemon_id
from rae.platforms.home.mapping import enrich_home_asset_label
from rae.core.mapping import load_mappings, match_asset
from rae.core.assets import Asset


def test_parse_home_pm_id():
    parsed = parse_home_pokemon_id("Models/Android/Mitake15sv/pokemons/pm0054_00_00_prefab.unity3d")
    assert parsed is not None
    assert parsed.number == 54
    assert parsed.canonical == "pm0054_00_00"


def test_parse_home_cap_id():
    parsed = parse_home_cap_id("external_files/files/tyranitar/cap0909_f00_s0_128.aba")
    assert parsed is not None
    assert parsed.number == 909


def test_parse_home_mitake_preview_id():
    parsed = parse_home_mitake_preview_id("external_files/files/tyranitar/mt_pv_ev_0718_02_00.aba")
    assert parsed is not None
    assert parsed.number == 718
    assert parsed.form_a == "02"


def test_parse_home_asset_id_prefers_pm():
    parsed = parse_home_asset_id("pm0906_00_00 and cap0906_f00")
    assert parsed is not None
    assert parsed.number == 906


def test_pokemon_home_mapping_matches_tyranitar_cap():
    mapping = next(m for m in load_mappings(platform="mobile") if m.mapping_id == "pokemon_home_android")
    asset = Asset(
        asset_id="x",
        virtual_path="mobile/pokemon_home/external_files/files/tyranitar/cap0054_f00_s0_128.aba",
        kind="test",
        magic="ABA",
        extension=".aba",
        data=b"{}",
        original_data=b"{}",
    )
    match = match_asset(asset, mapping)
    assert match is not None
    assert match.category == "models"
    enrich_home_asset_label(asset)
    assert "Psyduck" in asset.mapping_label
    assert "0054" in asset.mapping_label


def test_pokemon_home_mapping_matches_dependencies():
    mapping = next(m for m in load_mappings(platform="mobile") if m.mapping_id == "pokemon_home_android")
    asset = Asset(
        asset_id="y",
        virtual_path="mobile/pokemon_home/candidates/Models/Android/Mitake15sv/dependencies/pm0906_00_00",
        kind="test",
        magic="UNITY",
        extension=".unity3d",
        data=b"{}",
        original_data=b"{}",
    )
    match = match_asset(asset, mapping)
    assert match is not None
    assert "textures" in match.category or "rigs" in match.category
    enrich_home_asset_label(asset)
    assert "Sprigatito" in asset.mapping_label
