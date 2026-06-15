from rae.easyfind import (
    EasyFindCanvasFilters,
    build_cluster_detail_layout,
    build_cluster_overview_layout,
    create_easyfind_document,
)
from rae.scanner import Asset


def _asset(asset_id: str, path: str, magic: str, **kwargs) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind=magic,
        magic=magic,
        extension=".bin",
        data=b"x" * 8,
        original_data=b"x" * 8,
        **kwargs,
    )


def _large_color_document():
    assets = []
    for index in range(120):
        assets.append(_asset(f"m{index}", f"chars/{index}.bmd", "BMD0"))
    for index in range(80):
        assets.append(_asset(f"s{index}", f"sprites/{index}.ncer", "RECN"))
    return create_easyfind_document(assets=assets, rom_path="game.nds")


def test_cluster_overview_is_coarse():
    doc = _large_color_document()
    layout = build_cluster_overview_layout(
        doc,
        filters=EasyFindCanvasFilters(shown_types=frozenset({"model", "image_or_sprite_source"})),
    )
    assert not layout.empty
    assert layout.clusters
    assert layout.nodes == {}
    assert layout.groups == ()
    assert sum(cluster.node_count for cluster in layout.clusters) == len(doc.nodes)


def test_cluster_detail_only_materializes_members():
    doc = _large_color_document()
    overview = build_cluster_overview_layout(
        doc,
        filters=EasyFindCanvasFilters(shown_types=frozenset({"model", "image_or_sprite_source"})),
    )
    cluster = overview.clusters[0]
    detail = build_cluster_detail_layout(
        doc,
        filters=EasyFindCanvasFilters(shown_types=frozenset({"model", "image_or_sprite_source"})),
        cluster_id=cluster.cluster_id,
        member_ids=frozenset(cluster.node_ids),
    )
    assert len(detail.nodes) == cluster.node_count
    assert set(detail.nodes) == set(cluster.node_ids)
