"""Virtual BTX0 texture-slot asset expansion."""
from __future__ import annotations

from unittest.mock import patch

from rae.scanner import Asset
from rae.virtual_texture_assets import (
    expand_btx0_texture_slots,
    physical_assets,
    texture_slot_group_name,
)


def _btx_asset(asset_id: str = "btx1") -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path="a/0/8/1/file_0107.bin.nsbtx",
        kind="Texture archive",
        magic="BTX0",
        extension=".nsbtx",
        data=b"BTX0" + b"\x00" * 64,
        original_data=b"BTX0" + b"\x00" * 64,
    )


def test_texture_slot_group_name() -> None:
    assert texture_slot_group_name("princess.1", archive_stem="file_0107") == "princess"
    assert texture_slot_group_name("wall_tex", archive_stem="file_0107") == "file_0107"


@patch("rae.platforms.nds.virtual_texture_assets.parse_tex0_manifest")
def test_expand_btx0_texture_slots(mock_manifest) -> None:
    class Tex:
        def __init__(self, name: str) -> None:
            self.name = name

    class Manifest:
        textures = [Tex("princess.1"), Tex("princess.2"), Tex("princess.3")]

    mock_manifest.return_value = Manifest()
    parent = _btx_asset()
    expanded = expand_btx0_texture_slots([parent])
    assert len(expanded) == 4
    slots = [asset for asset in expanded if asset.is_texture_slot]
    assert len(slots) == 3
    assert slots[0].texture_slot == "princess.1"
    assert slots[0].parent_asset_id == parent.asset_id
    assert slots[0].data is parent.data


def test_physical_assets_strips_virtual_slots() -> None:
    parent = _btx_asset()
    slot = Asset(
        asset_id="btx1::slot::princess.1",
        virtual_path="a/0/8/1/file_0107.bin.nsbtx#princess.1",
        kind="Texture slot",
        magic="BTX0",
        extension=".nsbtx",
        data=parent.data,
        original_data=parent.data,
        parent_asset_id=parent.asset_id,
        texture_slot="princess.1",
        is_texture_slot=True,
    )
    assert physical_assets([parent, slot]) == [parent]
