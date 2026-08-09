"""HOME browser grouping: cache-ready species vs key-locked stubs."""
from __future__ import annotations

import json

from rae.core.assets import Asset
from rae.platforms.mobile.rom import (
    _HOME_LOCKED_FOLDER,
    _HOME_READY_FOLDER,
    _group_home_assets_by_availability,
)
from rae.platforms.mobile.model_module.preview import _home_rows_all_encrypted


def _home_asset(virtual_path: str, magic: str = "HOME") -> Asset:
    payload = json.dumps({"id": "pm0054_00_00"}).encode("utf-8")
    return Asset(
        asset_id="t",
        virtual_path=virtual_path,
        kind="test",
        magic=magic,
        extension=".json",
        data=payload,
        original_data=payload,
    )


def test_ready_species_moves_to_ready_folder():
    asset = _home_asset("pokemon_home/pm0054_00_00_Psyduck.homepkg")
    _group_home_assets_by_availability([asset], previewable={54})
    assert _HOME_READY_FOLDER in asset.virtual_path
    assert asset.virtual_path.startswith("pokemon_home/")


def test_locked_species_moves_to_locked_folder():
    asset = _home_asset("pokemon_home/pm0023_00_00_Ekans.homepkg")
    _group_home_assets_by_availability([asset], previewable={54})
    assert _HOME_LOCKED_FOLDER in asset.virtual_path


def test_non_species_rows_are_untouched():
    asset = _home_asset("pokemon_home/0 app UI assets.homeui", magic="HOMEUI")
    before = asset.virtual_path
    _group_home_assets_by_availability([asset], previewable={54})
    assert asset.virtual_path == before


def test_mobile_prefixed_paths_keep_app_segment():
    asset = _home_asset("mobile/pokemon_home/external_files/files/tyranitar/cap0054_f00_s0_128.aba", magic="ABA")
    _group_home_assets_by_availability([asset], previewable=set())
    assert asset.virtual_path.startswith(f"mobile/pokemon_home/{_HOME_LOCKED_FOLDER}/")


def test_all_encrypted_detection():
    rows = [{"virtual_path": "files/tyranitar/cap0023_f00_s0_128.aba"}]
    assert _home_rows_all_encrypted(rows)
    rows.append({"virtual_path": "files/Cache/AB/CAB-123"})
    assert not _home_rows_all_encrypted(rows)
    assert not _home_rows_all_encrypted([])
