from rae.easyfind import (
    EasyFindCanvasFilters,
    EasyFindLayoutOptions,
    build_source_level_canvas_layout,
    create_easyfind_document,
)
from rae.easyfind.color_buckets import BUCKET_LABELS
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


def _document_with_all_types():
    assets = [
        _asset("m1", "a/model.bmd", "BMD0", mapping_category="models", mapping_label="Hero"),
        _asset("t1", "a/tex.btx", "BTX0"),
        _asset("s1", "a/sprite.ncer", "RECN"),
        _asset("a1", "a/audio.sdat", "SDAT"),
        _asset("an1", "a/anim.nsbca", "BCA0"),
        _asset("ar1", "a/archive.narc", "NARC"),
        _asset("u1", "a/unknown.bin", "ZZZZ"),
    ]
    doc = create_easyfind_document(assets=assets, rom_path="game.nds")
    return doc


def test_type_group_layout():
    doc = _document_with_all_types()
    layout = build_source_level_canvas_layout(
        doc, filters=EasyFindCanvasFilters(group_by="type"),
    )
    titles = [section.title for section in layout.sections]
    assert "Models" in titles
    assert "Texture Archives" in titles
    assert "Images / Sprites" in titles
    assert "Audio" in titles
    assert sum(section.count for section in layout.sections) == len(doc.nodes)
    assert len(layout.nodes) == len(doc.nodes)
    assert layout.bounds.width >= 400
    assert layout.bounds.height > 0
    for node in doc.nodes:
        assert node.node_id in layout.nodes
    assert len(layout.groups) >= len(layout.sections)


def test_color_group_layout_has_outlines():
    doc = _document_with_all_types()
    layout = build_source_level_canvas_layout(
        doc, filters=EasyFindCanvasFilters(group_by="color"),
    )
    assert not layout.empty
    assert layout.groups
    top_groups = [g for g in layout.groups if g.level == 0]
    assert top_groups
    for group in top_groups:
        assert group.title in BUCKET_LABELS.values() or group.title
        assert group.accent_rgb
        assert group.width > 0 and group.height > 0


def test_mosaic_not_single_column():
    doc = _document_with_all_types()
    layout = build_source_level_canvas_layout(
        doc,
        filters=EasyFindCanvasFilters(group_by="type"),
        options=EasyFindLayoutOptions(max_row_width=2200),
    )
    if len(layout.sections) >= 2:
        xs = sorted(section.x for section in layout.sections)
        assert xs[-1] > xs[0]


def test_large_group_avoids_extreme_pillar():
    assets = [
        _asset(f"s{i}", f"sprites/chunk/{i}.ncer", "RECN")
        for i in range(150)
    ]
    doc = create_easyfind_document(assets=assets, rom_path="game.nds")
    layout = build_source_level_canvas_layout(
        doc,
        filters=EasyFindCanvasFilters(group_by="color"),
    )
    assert len(layout.nodes) == 150
    tallest = max(layout.groups, key=lambda group: group.height)
    assert tallest.width > 0
    assert tallest.height / tallest.width < 2.5


def test_deterministic_layout():
    doc = _document_with_all_types()
    filters = EasyFindCanvasFilters(group_by="type")
    options = EasyFindLayoutOptions()
    first = build_source_level_canvas_layout(doc, filters=filters, options=options)
    second = build_source_level_canvas_layout(doc, filters=filters, options=options)
    assert [(s.x, s.y, s.width, s.height) for s in first.sections] == [
        (s.x, s.y, s.width, s.height) for s in second.sections
    ]
    assert first.nodes == second.nodes
    assert first.bounds == second.bounds
