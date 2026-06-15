"""EasyFind indexing for BTX0 texture slots."""
from __future__ import annotations

from unittest.mock import patch

from rae.easyfind.build_index import assets_for_easyfind_index, create_easyfind_document
from rae.easyfind import save_easyfind, validate_easyfind
from tests.easyfind_testutil import finalize_easyfind_document
from rae.scanner import Asset


def _btx(asset_id: str = "btx1") -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path="a/0/8/1/file_0107.bin.nsbtx",
        kind="Texture archive",
        magic="BTX0",
        extension=".nsbtx",
        data=b"BTX0" + b"\x00" * 32,
        original_data=b"BTX0" + b"\x00" * 32,
    )


@patch("rae.easyfind.build_index.expand_btx0_texture_slots")
def test_easyfind_indexes_texture_slots_not_parent_archive(mock_expand) -> None:
    parent = _btx()
    slot_a = Asset(
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
    slot_b = Asset(
        asset_id="btx1::slot::princess.2",
        virtual_path="a/0/8/1/file_0107.bin.nsbtx#princess.2",
        kind="Texture slot",
        magic="BTX0",
        extension=".nsbtx",
        data=parent.data,
        original_data=parent.data,
        parent_asset_id=parent.asset_id,
        texture_slot="princess.2",
        is_texture_slot=True,
    )
    mock_expand.return_value = [parent, slot_a, slot_b]

    doc = create_easyfind_document(assets=[parent], rom_path="game.nds")
    node_labels = {node.label for node in doc.nodes}
    assert "princess.1" in node_labels
    assert "princess.2" in node_labels
    assert "a/0/8/1/file_0107.bin.nsbtx" not in node_labels
    kinds = {node.node_kind for node in doc.nodes}
    assert "texture_slot" in kinds
    assert kinds.isdisjoint({"texture_archive"}) or "texture_archive" not in kinds


@patch("rae.easyfind.build_index.expand_btx0_texture_slots")
def test_btx0_slot_asset_refs_validate_after_save(mock_expand, tmp_path) -> None:
    parent = _btx()
    slot_a = Asset(
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
    mock_expand.return_value = [parent, slot_a]

    doc = create_easyfind_document(assets=[parent], rom_path="game.nds")
    slot_node = next(node for node in doc.nodes if node.label == "princess.1")
    assert slot_node.asset_refs[0].asset_id == slot_a.asset_id
    assert slot_node.metadata.get("parent_asset_id") == parent.asset_id

    path = save_easyfind(tmp_path / "slots.easyfind", finalize_easyfind_document(doc))
    report = validate_easyfind(path)
    assert report.ok, report.errors[:5]
