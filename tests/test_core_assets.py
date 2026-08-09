"""Tests for core Asset type."""
from __future__ import annotations

from rae.core.assets import Asset
from rae.platforms.home.library import home_packages_as_rae_assets
from rae.scanner import Asset as ScannerAsset


def test_asset_importable_from_core_and_scanner_shim():
    asset = Asset(
        asset_id="t",
        virtual_path="test/path",
        kind="test",
        magic="HOME",
        extension=".bin",
        data=b"{}",
        original_data=b"{}",
    )
    assert asset.size == 2
    assert asset.folder_key == "test"
    assert ScannerAsset is Asset


def test_home_library_uses_core_asset_not_nds():
    import inspect

    source = inspect.getsourcefile(home_packages_as_rae_assets)
    assert source is not None
    from rae.platforms import home

    library_path = home.library.__file__
    text = open(library_path, encoding="utf-8").read()
    assert "platforms.nds.scanner" not in text
    assert "core.assets" in text
