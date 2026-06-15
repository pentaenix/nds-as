"""BTX0 thumbnail texture slot selection."""
from __future__ import annotations

from unittest.mock import patch

from rae.btx0_preview_selection import choose_btx0_thumbnail_texture_name
from rae.scanner import Asset


def _btx(asset_id: str = "b1", **kwargs) -> Asset:
    defaults = dict(
        asset_id=asset_id,
        virtual_path="a/0/8/1/file_0107.bin.nsbtx",
        kind="Texture archive",
        magic="BTX0",
        extension=".nsbtx",
        data=b"BTX0",
        original_data=b"BTX0",
    )
    defaults.update(kwargs)
    return Asset(**defaults)


@patch("rae.platforms.nds.btx0_preview_selection.btx0_texture_entry_names")
def test_prefers_down_facing_slot_for_single_character_sheet(mock_names) -> None:
    mock_names.return_value = [
        "princess.1",
        "princess.2",
        "princess.3",
        "princess.4",
    ]
    assert choose_btx0_thumbnail_texture_name(_btx()) == "princess.2"


def test_texture_slot_asset_uses_exact_slot() -> None:
    asset = _btx(
        asset_id="b1::slot::princess.3",
        virtual_path="a/0/8/1/file_0107.bin.nsbtx#princess.3",
        is_texture_slot=True,
        texture_slot="princess.3",
        parent_asset_id="b1",
    )
    assert choose_btx0_thumbnail_texture_name(asset) == "princess.3"
