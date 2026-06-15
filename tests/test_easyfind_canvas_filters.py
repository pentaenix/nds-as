from rae.easyfind import (
    EasyFindCanvasFilters,
    build_source_level_canvas_layout,
    create_easyfind_document,
    filter_nodes,
    load_node_index,
)
from rae.easyfind.canvas_filters import FOCUS_OP_AND, FOCUS_OP_OFF, FOCUS_OP_OR, focus_overview_group_by
from tests.easyfind_testutil import finalize_easyfind_document
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


def _sample_document():
    assets = [
        _asset("m1", "chars/hero/model.bmd", "BMD0", mapping_category="characters", mapping_label="Hero"),
        _asset("t1", "world/town/tex.btx", "BTX0", mapping_label="Town"),
        _asset("s1", "sprites/ui/icon.ncer", "RECN"),
        _asset("a1", "audio/bgm/theme.sdat", "SDAT"),
        _asset("u1", "misc/unknown.bin", "ZZZZ"),
    ]
    doc = finalize_easyfind_document(
        create_easyfind_document(assets=assets, rom_path="game.nds"),
    )
    doc.nodes[0].preview_ref = "prev1"
    return doc


def test_type_filter():
    doc = _sample_document()
    models = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"unknown"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=load_node_index(doc),
    )
    assert all(n.node_kind == "model" for n in models)
    assert len(models) == 1

    index = load_node_index(doc)
    sprites = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"unknown"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_type="image_or_sprite_source",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(sprites) == 1

    audio = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"audio"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_type="audio",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(audio) == 1

    unknown = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"unknown"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_type="unknown",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(unknown) == 1


def test_hidden_types_filter():
    doc = _sample_document()
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(hidden_types=frozenset({"audio", "unknown"})),
    )
    kinds = {n.node_kind for n in visible}
    assert "audio" not in kinds
    assert "unknown" not in kinds
    assert len(visible) == 3


def test_shown_types_filter():
    doc = _sample_document()
    assert filter_nodes(doc, EasyFindCanvasFilters(shown_types=frozenset())) == []

    models_only = filter_nodes(
        doc,
        EasyFindCanvasFilters(shown_types=frozenset({"model"})),
    )
    assert len(models_only) == 1
    assert models_only[0].node_kind == "model"

    mixed = filter_nodes(
        doc,
        EasyFindCanvasFilters(shown_types=frozenset({"model", "texture_archive"})),
    )
    kinds = {n.node_kind for n in mixed}
    assert kinds == {"model", "texture_archive"}


def test_renderable_filter():
    doc = _sample_document()
    with_preview = filter_nodes(doc, EasyFindCanvasFilters(renderable_filter="with_preview"))
    assert len(with_preview) == 1
    without = filter_nodes(doc, EasyFindCanvasFilters(renderable_filter="without_preview"))
    assert len(without) == len(doc.nodes) - 1


def test_empty_filter_result_layout():
    doc = _sample_document()
    layout = build_source_level_canvas_layout(
        doc,
        filters=EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_type="animation",
            focus_type_op=FOCUS_OP_AND,
        ),
    )
    assert layout.empty is True
    assert layout.sections == ()
    assert layout.nodes == {}


def test_focus_type_only_without_colors():
    doc = _sample_document()
    index = load_node_index(doc)
    models = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(models) == 1
    assert models[0].node_kind == "model"


def test_focus_mode_ignores_organize_types():
    doc = _sample_document()
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"audio"}),
            shown_types=frozenset(),
        ),
        index=index,
    )
    assert len(visible) == 1
    assert visible[0].node_kind == "audio"


def test_focus_primary_colors_union():
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    for node in doc.nodes:
        bucket = "blue" if node.node_kind == "model" else "red"
        doc.color_signatures.append(
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket=bucket,
                dominant_colors=["#0000ff" if bucket == "blue" else "#ff0000"],
                secondary_buckets=[],
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        )
        node.color_signature_ref = f"sig:{node.node_id}"

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red", "blue"}),
        ),
        index=index,
    )
    assert len(visible) == len(doc.nodes)


def test_focus_primary_color_filter():
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    model_node = next(n for n in doc.nodes if n.node_kind == "model")
    doc.color_signatures = [
        EasyFindColorSignature(
            signature_id=f"sig:{model_node.node_id}",
            node_id=model_node.node_id,
            dominant_bucket="blue",
            dominant_colors=["#0000ff"],
            secondary_buckets=["cyan"],
            brightness="mid",
            saturation="high",
            transparent_ratio=0.0,
            metadata={},
        )
    ]
    model_node.color_signature_ref = f"sig:{model_node.node_id}"

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    blue_only = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"blue"}),
        ),
        index=index,
    )
    assert len(blue_only) == 1
    assert blue_only[0].node_kind == "model"

    red_only = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red"}),
        ),
        index=index,
    )
    assert len(red_only) == 0


def test_focus_secondary_color_filter():
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    model_node = next(n for n in doc.nodes if n.node_kind == "model")
    doc.color_signatures = [
        EasyFindColorSignature(
            signature_id=f"sig:{model_node.node_id}",
            node_id=model_node.node_id,
            dominant_bucket="blue",
            dominant_colors=["#0000ff"],
            secondary_buckets=["cyan", "white"],
            brightness="mid",
            saturation="high",
            transparent_ratio=0.0,
            metadata={},
        )
    ]
    model_node.color_signature_ref = f"sig:{model_node.node_id}"

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    cyan = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"blue"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_secondary_colors=frozenset({"cyan"}),
            focus_secondary_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(cyan) == 1

    pink = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"blue"}),
            focus_primary_op=FOCUS_OP_OR,
            focus_secondary_colors=frozenset({"pink"}),
            focus_secondary_op=FOCUS_OP_OR,
        ),
        index=index,
    )
    assert len(pink) == 1
    assert pink[0].node_kind == "model"


def test_focus_primary_and_secondary_same_color_unions():
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    for node in doc.nodes:
        dominant = "red" if node.node_kind == "model" else "blue"
        secondary = ["red"] if node.node_kind == "texture_archive" else []
        doc.color_signatures.append(
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket=dominant,
                dominant_colors=["#ff0000" if dominant == "red" else "#0000ff"],
                secondary_buckets=secondary,
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        )
        node.color_signature_ref = f"sig:{node.node_id}"

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red"}),
            focus_primary_op=FOCUS_OP_OR,
            focus_secondary_colors=frozenset({"red"}),
            focus_secondary_op=FOCUS_OP_OR,
        ),
        index=index,
    )
    kinds = {n.node_kind for n in visible}
    assert "model" in kinds
    assert "texture_archive" in kinds
    assert len(visible) == 2

    models_only = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red"}),
            focus_primary_op=FOCUS_OP_OR,
            focus_secondary_colors=frozenset({"red"}),
            focus_secondary_op=FOCUS_OP_OR,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(models_only) == 1
    assert models_only[0].node_kind == "model"


def test_focus_secondary_green_only_with_model_type():
    """No primary filter: any main color, but green must be an accent (secondary) color."""
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    for node in doc.nodes:
        if node.node_kind != "model":
            continue
        doc.color_signatures = [
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket="brown",
                dominant_colors=["#8b4513"],
                secondary_buckets=["green", "gray"],
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        ]
        node.color_signature_ref = f"sig:{node.node_id}"
        break

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_secondary_colors=frozenset({"green"}),
            focus_secondary_op=FOCUS_OP_AND,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(visible) == 1
    assert visible[0].node_kind == "model"

    green_primary = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"green"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(green_primary) == 0


def test_focus_green_primary_and_red_secondary_and_model_type():
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    for node in doc.nodes:
        if node.node_kind != "model":
            continue
        doc.color_signatures = [
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket="green",
                dominant_colors=["#00ff00"],
                secondary_buckets=["red"],
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        ]
        node.color_signature_ref = f"sig:{node.node_id}"
        break

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"green"}),
            focus_primary_op=FOCUS_OP_AND,
            focus_secondary_colors=frozenset({"red"}),
            focus_secondary_op=FOCUS_OP_AND,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(visible) == 1
    assert visible[0].node_kind == "model"


def test_focus_all_clauses_off_shows_every_node():
    doc = _sample_document()
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_op="-",
            focus_secondary_op="-",
            focus_type_op="-",
        ),
        index=index,
    )
    assert len(visible) == len(doc.nodes)


def test_focus_dash_applies_checked_primary_color():
    doc = _sample_document()
    from rae.easyfind.models import EasyFindColorSignature

    for node in doc.nodes:
        bucket = "green" if node.node_kind == "model" else "red"
        doc.color_signatures.append(
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket=bucket,
                dominant_colors=["#00ff00" if bucket == "green" else "#ff0000"],
                secondary_buckets=[],
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        )
        node.color_signature_ref = f"sig:{node.node_id}"

    finalize_easyfind_document(doc)
    index = load_node_index(doc)
    visible = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"green"}),
            focus_primary_op=FOCUS_OP_OFF,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(visible) == 1
    assert visible[0].node_kind == "model"
    assert focus_overview_group_by(
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"green"}),
            focus_primary_op=FOCUS_OP_OFF,
        )
    ) == "color"
