from rae.easyfind import EasyFindCanvasFilters, create_easyfind_document, filter_nodes, load_node_index
from rae.easyfind.models import EasyFindColorSignature
from rae.scanner import Asset
from tests.easyfind_testutil import finalize_easyfind_document


def _asset(asset_id: str, path: str, magic: str) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind=magic,
        magic=magic,
        extension=".bin",
        data=b"x" * 8,
        original_data=b"x" * 8,
    )


def test_node_index_bucket_lookup_is_direct():
    assets = [_asset(f"a{i}", f"tex/{i}.btx", "BTX0") for i in range(20)]
    doc = create_easyfind_document(assets=assets, rom_path="game.nds")
    for index, node in enumerate(doc.nodes):
        bucket = "red" if index < 5 else "blue"
        doc.color_signatures.append(
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket=bucket,
                dominant_colors=["#ff0000" if bucket == "red" else "#0000ff"],
                secondary_buckets=[],
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        )
        node.color_signature_ref = f"sig:{node.node_id}"

    doc = finalize_easyfind_document(doc)
    index = load_node_index(doc)
    assert len(index.by_primary_bucket["red"]) == 5
    assert len(index.by_primary_bucket["blue"]) == 15

    red_nodes = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red"}),
        ),
        index=index,
    )
    assert len(red_nodes) == 5
