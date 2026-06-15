"""Browser asset search indexing and filtering."""
from __future__ import annotations

from unittest.mock import patch

from rae.scanner import Asset, asset_search_text, filter_assets_indexed


def _asset(asset_id: str, path: str, magic: str, data: bytes = b"x") -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind="Texture archive" if magic == "BTX0" else "Model",
        magic=magic,
        extension=".nsbtx" if magic == "BTX0" else ".nsbmd",
        data=data,
        original_data=data,
    )


def test_filter_matches_texture_dictionary_names() -> None:
    asset = _asset("tex1", "a/0/8/1/file_0107.bin.nsbtx", "BTX0")
    search = {
        asset.asset_id: (
            "a/0/8/1/file_0107.bin.nsbtx texture archive btx0 textures "
            "nitro 3d texture packs princess.1 princess.10"
        ),
    }
    matched = filter_assets_indexed([asset], "princess", search)
    assert matched == [asset]


def test_asset_search_text_includes_dictionary_names() -> None:
    asset = _asset("tex1", "a/0/8/1/file_0107.bin.nsbtx", "BTX0")
    with patch(
        "rae.platforms.nds.nitro_names.asset_dictionary_names",
        return_value=["princess.1", "princess.10"],
    ):
        text = asset_search_text(asset)
    assert "princess.1" in text
    assert "file_0107.bin" in text
