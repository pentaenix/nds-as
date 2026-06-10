import zipfile

from rae.easyfind import create_easyfind_document, load_easyfind_quick_open, save_easyfind
from rae.scanner import Asset


def _asset(asset_id: str) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=f"{asset_id}.nsbmd",
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0",
        original_data=b"BMD0",
    )


def test_quick_open_reads_only_metadata(tmp_path):
    doc = create_easyfind_document(
        assets=[_asset("a1"), _asset("a2")],
        rom_path="game.nds",
        rom_title="Title",
        rom_game_code="CODE",
    )
    path = save_easyfind(tmp_path / "game.easyfind", doc)

    with zipfile.ZipFile(path, "r") as zf:
        names = set(zf.namelist())
        assert "index/assets.jsonl" in names
        assert "index/nodes.jsonl" in names

    quick = load_easyfind_quick_open(path)
    assert quick.format == "rae-easyfind-v1"
    assert quick.schema_version == 1
    assert quick.counts["assets"] == 2
    assert quick.counts["nodes"] == 2
    assert quick.source["rom_name"] == "game.nds"
    assert quick.source["rom_title"] == "Title"
    assert quick.source["rom_game_code"] == "CODE"
    assert quick.default_view["group_by"] == ["color", "type"]
    assert quick.capabilities["contains_previews"] is True
