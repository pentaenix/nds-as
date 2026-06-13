"""EasyFind canvas filtering and grouping (Qt-free)."""
from __future__ import annotations

from dataclasses import dataclass

from .color_buckets import BUCKET_ORDER, bucket_label
from .models import EasyFindAssetRef, EasyFindDocument, EasyFindNode

SECTION_ORDER: tuple[str, ...] = (
    "model",
    "texture_slot",
    "texture_archive",
    "image_or_sprite_source",
    "audio",
    "animation",
    "archive",
    "unknown",
)

SECTION_DISPLAY_NAMES: dict[str, str] = {
    "model": "Models",
    "texture_slot": "Texture Slots",
    "texture_archive": "Texture Archives",
    "image_or_sprite_source": "Images / Sprites",
    "audio": "Audio",
    "animation": "Animations",
    "archive": "Archives",
    "unknown": "Unknown",
}

TYPE_FILTER_OPTIONS: dict[str, str] = {
    "all": "All Types",
    "model": "Models",
    "texture_slot": "Texture Slots",
    "texture_archive": "Texture Archives",
    "image_or_sprite_source": "Images / Sprites",
    "audio": "Audio",
    "animation": "Animations",
    "archive": "Archives",
    "unknown": "Unknown",
}

GROUP_BY_OPTIONS: dict[str, str] = {
    "color": "Primary Color",
    "type": "Asset Type",
    "color_type": "Color → Type",
    "type_color": "Type → Color",
}

# Nested grouping keys per mode (deepest level last).
GROUPING_LEVELS: dict[str, tuple[str, ...]] = {
    "color": ("color", "type"),
    "type": ("type", "color"),
    "color_type": ("color", "type"),
    "type_color": ("type", "color"),
}

DEFAULT_HIDDEN_MAGICS: frozenset[str] = frozenset({"RLCN"})

FOCUS_ANY = "any"
FILTER_MODE_ORGANIZE = "organize"
FILTER_MODE_FOCUS = "focus"

FOCUS_COLOR_OPTIONS: dict[str, str] = {
    FOCUS_ANY: "— Any —",
    "red": "Red",
    "orange": "Orange",
    "yellow": "Yellow",
    "green": "Green",
    "cyan": "Cyan",
    "blue": "Blue",
    "purple": "Purple",
    "pink": "Pink",
    "brown": "Brown",
    "gray": "Gray",
    "white": "White",
    "black": "Black",
    "neutral": "Neutral",
}


@dataclass
class EasyFindCanvasFilters:
    group_by: str = "color"
    filter_mode: str = FILTER_MODE_ORGANIZE
    renderable_filter: str = "all"
    hidden_types: frozenset[str] = frozenset()
    hidden_magics: frozenset[str] = DEFAULT_HIDDEN_MAGICS
    primary_color: str = FOCUS_ANY
    secondary_color: str = FOCUS_ANY
    focus_primary_colors: frozenset[str] = frozenset()
    focus_secondary_colors: frozenset[str] = frozenset()
    focus_type: str = FOCUS_ANY
    type_filter: str = FOCUS_ANY  # backward compat alias for focus_type
    # When set, only these node kinds appear. ``None`` skips the gate (tests/legacy).
    # An empty frozenset means show nothing on the canvas.
    shown_types: frozenset[str] | None = None


def section_display_name(node_kind: str) -> str:
    return SECTION_DISPLAY_NAMES.get(node_kind, node_kind.replace("_", " ").title())


def _asset_for_node(document: EasyFindDocument, node: EasyFindNode) -> EasyFindAssetRef | None:
    if not node.asset_refs:
        return None
    asset_id = node.asset_refs[0].asset_id
    for asset in document.assets:
        if asset.asset_id == asset_id:
            return asset
    return None


def _signature_for_node(document: EasyFindDocument, node: EasyFindNode):
    ref = node.color_signature_ref
    if not ref:
        return None
    for sig in document.color_signatures:
        if sig.signature_id == ref or sig.node_id == node.node_id:
            return sig
    return None


def node_color_bucket(document: EasyFindDocument, node: EasyFindNode) -> str:
    sig = _signature_for_node(document, node)
    if sig is not None:
        return sig.dominant_bucket
    asset = _asset_for_node(document, node)
    magic = asset.magic if asset else str(node.metadata.get("magic", ""))
    if node.node_kind == "audio":
        return "audio"
    if magic.upper() == "RLCN":
        return "neutral"
    return "unknown"


def node_group_key(document: EasyFindDocument, node: EasyFindNode, level: str) -> str:
    if level == "color":
        return node_color_bucket(document, node)
    if level == "type":
        return node.node_kind
    if level == "magic":
        asset = _asset_for_node(document, node)
        magic = asset.magic if asset else str(node.metadata.get("magic", "?"))
        return magic.upper() or "?"
    return node.node_id


def node_group_title(document: EasyFindDocument, node: EasyFindNode, level: str, key: str) -> str:
    if level == "color":
        return bucket_label(key)
    if level == "type":
        return section_display_name(key)
    return key


def node_sort_key(document: EasyFindDocument, node: EasyFindNode) -> tuple:
    asset = _asset_for_node(document, node)
    if asset is not None:
        return (
            asset.mapping_category or "",
            asset.mapping_label or "",
            asset.virtual_path or node.label,
            asset.asset_id,
        )
    return ("", "", node.label, node.node_id)


def node_matches_type_filter(node: EasyFindNode, type_filter: str) -> bool:
    if type_filter in {FOCUS_ANY, "all"}:
        return True
    return node.node_kind == type_filter


def node_matches_focus_type(node: EasyFindNode, focus_type: str) -> bool:
    if focus_type in {FOCUS_ANY, "all"}:
        return True
    return node.node_kind == focus_type


def node_matches_primary_color(
    document: EasyFindDocument,
    node: EasyFindNode,
    primary_color: str,
) -> bool:
    if primary_color in {FOCUS_ANY, "all", ""}:
        return True
    sig = _signature_for_node(document, node)
    if sig is None:
        return primary_color == node_color_bucket(document, node)
    return sig.dominant_bucket == primary_color


def node_matches_secondary_color(
    document: EasyFindDocument,
    node: EasyFindNode,
    secondary_color: str,
) -> bool:
    if secondary_color in {FOCUS_ANY, "all", ""}:
        return True
    sig = _signature_for_node(document, node)
    if sig is None:
        return False
    return secondary_color in sig.secondary_buckets


def _resolved_focus_type(filters: EasyFindCanvasFilters) -> str:
    focus = filters.focus_type
    if focus not in {FOCUS_ANY, "all", ""}:
        return focus
    legacy = filters.type_filter
    if legacy not in {FOCUS_ANY, "all", ""}:
        return legacy
    return FOCUS_ANY


def node_matches_shown_types(node: EasyFindNode, shown_types: frozenset[str] | None) -> bool:
    if shown_types is None:
        return True
    if not shown_types:
        return False
    return node.node_kind in shown_types


def _resolved_focus_primary_colors(filters: EasyFindCanvasFilters) -> frozenset[str]:
    if filters.focus_primary_colors:
        return filters.focus_primary_colors
    if filters.primary_color not in {FOCUS_ANY, "all", ""}:
        return frozenset({filters.primary_color})
    return frozenset()


def _resolved_focus_secondary_colors(filters: EasyFindCanvasFilters) -> frozenset[str]:
    if filters.focus_secondary_colors:
        return filters.focus_secondary_colors
    if filters.secondary_color not in {FOCUS_ANY, "all", ""}:
        return frozenset({filters.secondary_color})
    return frozenset()


def filter_nodes(
    document: EasyFindDocument,
    filters: EasyFindCanvasFilters,
    *,
    index: object | None = None,
) -> list[EasyFindNode]:
    """Return nodes visible under the current canvas filters."""
    if filters.filter_mode == FILTER_MODE_FOCUS:
        from .node_index import EasyFindNodeIndex, load_node_index

        node_index = index if isinstance(index, EasyFindNodeIndex) else load_node_index(document)
        return _filter_nodes_focus(document, filters, node_index)

    return _filter_nodes_organize(document, filters)


def _filter_nodes_focus(
    document: EasyFindDocument,
    filters: EasyFindCanvasFilters,
    index: object,
) -> list[EasyFindNode]:
    from .node_index import (
        EasyFindNodeIndex,
        nodes_for_node_kind,
        nodes_for_primary_colors,
        nodes_for_secondary_colors,
    )

    if not isinstance(index, EasyFindNodeIndex):
        return []

    primary_colors = _resolved_focus_primary_colors(filters)
    secondary_colors = _resolved_focus_secondary_colors(filters)
    focus_type = _resolved_focus_type(filters)
    candidate_ids: frozenset[str] | None = None

    if primary_colors:
        candidate_ids = nodes_for_primary_colors(index, primary_colors)
    if secondary_colors:
        secondary_ids = nodes_for_secondary_colors(index, secondary_colors)
        candidate_ids = secondary_ids if candidate_ids is None else candidate_ids & secondary_ids
    if focus_type not in {FOCUS_ANY, "all", ""}:
        type_ids = nodes_for_node_kind(index, focus_type)
        candidate_ids = type_ids if candidate_ids is None else candidate_ids & type_ids

    if candidate_ids is None or not candidate_ids:
        return []

    visible = [
        index.nodes_by_id[node_id]
        for node_id in candidate_ids
        if node_id in index.nodes_by_id
    ]
    visible.sort(key=lambda n: node_sort_key(document, n))
    return visible


def _filter_nodes_organize(document: EasyFindDocument, filters: EasyFindCanvasFilters) -> list[EasyFindNode]:
    visible: list[EasyFindNode] = []
    for node in document.nodes:
        if not node_matches_hidden_magics(document, node, filters.hidden_magics):
            continue
        if filters.shown_types is not None:
            if not node_matches_shown_types(node, filters.shown_types):
                continue
        elif not node_matches_hidden_types(node, filters.hidden_types):
            continue
        if not node_matches_renderable_filter(node, filters.renderable_filter):
            continue
        visible.append(node)
    visible.sort(key=lambda n: node_sort_key(document, n))
    return visible


def node_matches_hidden_types(node: EasyFindNode, hidden: frozenset[str]) -> bool:
    if not hidden:
        return True
    return node.node_kind not in hidden


def node_matches_hidden_magics(
    document: EasyFindDocument,
    node: EasyFindNode,
    hidden: frozenset[str],
) -> bool:
    if not hidden:
        return True
    asset = _asset_for_node(document, node)
    magic = (asset.magic if asset else str(node.metadata.get("magic", ""))).upper()
    return magic not in hidden


def node_matches_renderable_filter(node: EasyFindNode, renderable_filter: str) -> bool:
    if renderable_filter == "all":
        return True
    has_preview = bool(node.preview_ref)
    if renderable_filter == "with_preview":
        return has_preview
    if renderable_filter == "without_preview":
        return not has_preview
    return True


def group_nodes_by_kind(nodes: list[EasyFindNode]) -> dict[str, list[EasyFindNode]]:
    groups: dict[str, list[EasyFindNode]] = {kind: [] for kind in SECTION_ORDER}
    for node in nodes:
        groups.setdefault(node.node_kind, []).append(node)
    return groups


def group_key_sort_order(level: str, key: str) -> tuple:
    if level == "color":
        try:
            return (BUCKET_ORDER.index(key), key)
        except ValueError:
            return (len(BUCKET_ORDER), key)
    if level == "type":
        try:
            return (SECTION_ORDER.index(key), key)
        except ValueError:
            return (len(SECTION_ORDER), key)
    return (key,)
