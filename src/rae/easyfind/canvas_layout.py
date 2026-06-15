"""EasyFind canvas layout engine (Qt-free)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .canvas_filters import (
    GROUPING_LEVELS,
    EasyFindCanvasFilters,
    filter_nodes,
    group_key_sort_order,
    node_group_key,
    node_group_title,
)
from .color_buckets import bucket_rgb
from .models import EasyFindDocument, EasyFindNode


@dataclass(frozen=True)
class EasyFindLayoutOptions:
    group_by: str = "color"
    tile_width: int = 96
    tile_height: int = 96
    tile_gap: int = 8
    group_padding: int = 16
    group_header_height: int = 28
    group_gap: int = 24
    max_row_width: int = 3600
    max_group_inner_width: int = 2800
    max_group_inner_width_cap: int = 5600
    max_group_inner_rows: int = 10
    leaf_chunk_size: int = 72


@dataclass(frozen=True)
class EasyFindNodePlacement:
    node_id: str
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class EasyFindGroupPlacement:
    group_id: str
    title: str
    level: int
    accent_rgb: tuple[int, int, int]
    x: float
    y: float
    width: float
    height: float
    node_ids: tuple[str, ...]
    child_group_ids: tuple[str, ...]


@dataclass(frozen=True)
class EasyFindSectionPlacement:
    section_id: str
    title: str
    count: int
    x: float
    y: float
    width: float
    height: float
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class EasyFindClusterPlacement:
    """High-level portal tile; detail layout is computed when the cluster is opened."""

    cluster_id: str
    title: str
    level_name: str
    key: str
    level_index: int
    accent_rgb: tuple[int, int, int]
    x: float
    y: float
    width: float
    height: float
    node_count: int
    node_ids: tuple[str, ...]


@dataclass(frozen=True)
class EasyFindRect:
    x: float
    y: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.x + self.width

    @property
    def bottom(self) -> float:
        return self.y + self.height


@dataclass(frozen=True)
class EasyFindCanvasLayout:
    groups: tuple[EasyFindGroupPlacement, ...]
    sections: tuple[EasyFindSectionPlacement, ...]
    nodes: dict[str, EasyFindNodePlacement]
    bounds: EasyFindRect
    empty: bool = False
    clusters: tuple[EasyFindClusterPlacement, ...] = ()


@dataclass
class _LayoutNode:
    group_id: str
    title: str
    level_name: str
    level_index: int
    accent_rgb: tuple[int, int, int]
    width: float
    height: float
    local_x: float = 0.0
    local_y: float = 0.0
    node_ids: tuple[str, ...] = ()
    node_placements: dict[str, EasyFindNodePlacement] = field(default_factory=dict)
    children: list[_LayoutNode] = field(default_factory=list)


def _cell_size(options: EasyFindLayoutOptions) -> int:
    return options.tile_width + options.tile_gap


def _max_columns(options: EasyFindLayoutOptions, *, width_budget: int | None = None) -> int:
    cell = _cell_size(options)
    budget = width_budget or options.max_group_inner_width_cap
    return max(1, budget // cell)


def _columns_for_count(count: int, options: EasyFindLayoutOptions) -> int:
    """Pick a column count that keeps node grids squarish, not tall pillars."""
    if count <= 1:
        return 1
    cell = _cell_size(options)
    max_cols = _max_columns(options)

    # Enough columns to stay under the row cap.
    cols_for_row_cap = max(1, math.ceil(count / max(1, options.max_group_inner_rows)))
    # Classic squarish grid estimate.
    sqrt_cols = max(1, int(math.ceil(math.sqrt(count * 1.45))))
    cols = max(cols_for_row_cap, sqrt_cols)

    # Allow the inner region to grow wider for dense groups.
    needed_width = cols * cell + options.group_padding * 2
    width_budget = min(
        options.max_group_inner_width_cap,
        max(options.max_group_inner_width, needed_width),
    )
    max_cols = max(1, width_budget // cell)
    return min(max_cols, cols)


def _layout_node_grid(
    nodes: list[EasyFindNode],
    *,
    options: EasyFindLayoutOptions,
    origin_x: float,
    origin_y: float,
) -> tuple[float, float, dict[str, EasyFindNodePlacement]]:
    if not nodes:
        return 0.0, 0.0, {}

    cols = _columns_for_count(len(nodes), options)
    rows = max(1, math.ceil(len(nodes) / cols))
    inner_w = cols * options.tile_width + max(0, cols - 1) * options.tile_gap
    inner_h = rows * options.tile_height + max(0, rows - 1) * options.tile_gap

    placements: dict[str, EasyFindNodePlacement] = {}
    for index, node in enumerate(nodes):
        col = index % cols
        row = index // cols
        placements[node.node_id] = EasyFindNodePlacement(
            node_id=node.node_id,
            x=origin_x + col * _cell_size(options),
            y=origin_y + row * (options.tile_height + options.tile_gap),
            width=float(options.tile_width),
            height=float(options.tile_height),
        )
    return inner_w, inner_h, placements


def _layout_leaf_grid(
    document: EasyFindDocument,
    nodes: list[EasyFindNode],
    *,
    level_name: str,
    key: str,
    level_index: int,
    group_id: str,
    options: EasyFindLayoutOptions,
    title: str | None = None,
) -> _LayoutNode:
    pad = options.group_padding
    header = options.group_header_height
    inner_x = pad
    inner_y = pad + header
    inner_w, inner_h, placements = _layout_node_grid(
        nodes, options=options, origin_x=inner_x, origin_y=inner_y,
    )
    return _LayoutNode(
        group_id=group_id,
        title=title or node_group_title(document, nodes[0], level_name, key),
        level_name=level_name,
        level_index=level_index,
        accent_rgb=_accent(level_name, key),
        width=max(inner_w + pad * 2, 100.0),
        height=max(inner_h + pad * 2 + header, header + pad * 2 + options.tile_height),
        node_ids=tuple(n.node_id for n in nodes),
        node_placements=placements,
    )


def _accent(level_name: str, key: str) -> tuple[int, int, int]:
    if level_name == "color":
        return bucket_rgb(key)
    return (105, 105, 105)


def _split_nodes(document: EasyFindDocument, nodes: list[EasyFindNode], level_name: str) -> dict[str, list[EasyFindNode]]:
    buckets: dict[str, list[EasyFindNode]] = {}
    for node in nodes:
        key = node_group_key(document, node, level_name)
        buckets.setdefault(key, []).append(node)
    return buckets


def _pack_children(
    children: list[_LayoutNode],
    *,
    level_name: str,
    key: str,
    level_index: int,
    group_id: str,
    document: EasyFindDocument,
    sample_nodes: list[EasyFindNode],
    options: EasyFindLayoutOptions,
    row_width: int | None = None,
) -> _LayoutNode:
    pad = options.group_padding
    header = options.group_header_height
    gap = options.group_gap
    target_row_w = row_width or options.max_row_width

    x_cursor = pad
    y_cursor = pad + header
    row_h = 0.0
    max_right = pad
    max_bottom = y_cursor

    for child in children:
        if x_cursor + child.width > target_row_w and x_cursor > pad:
            x_cursor = pad
            y_cursor += row_h + gap
            row_h = 0.0
        child.local_x = x_cursor
        child.local_y = y_cursor
        x_cursor += child.width + gap
        row_h = max(row_h, child.height)
        max_right = max(max_right, child.local_x + child.width)
        max_bottom = max(max_bottom, child.local_y + child.height)

    inner_w = max_right + pad - pad
    inner_h = max_bottom - (pad + header) + pad

    return _LayoutNode(
        group_id=group_id,
        title=node_group_title(document, sample_nodes[0], level_name, key),
        level_name=level_name,
        level_index=level_index,
        accent_rgb=_accent(level_name, key),
        width=inner_w + pad,
        height=inner_h + pad + header,
        children=children,
    )


def _layout_leaf(
    document: EasyFindDocument,
    nodes: list[EasyFindNode],
    *,
    level_name: str,
    key: str,
    level_index: int,
    group_id: str,
    options: EasyFindLayoutOptions,
) -> _LayoutNode:
    chunk = max(24, options.leaf_chunk_size)
    if len(nodes) <= chunk:
        return _layout_leaf_grid(
            document,
            nodes,
            level_name=level_name,
            key=key,
            level_index=level_index,
            group_id=group_id,
            options=options,
        )

    children: list[_LayoutNode] = []
    for index, start in enumerate(range(0, len(nodes), chunk)):
        part = nodes[start:start + chunk]
        children.append(_layout_leaf_grid(
            document,
            part,
            level_name=level_name,
            key=key,
            level_index=level_index,
            group_id=f"{group_id}/chunk/{index}",
            options=options,
            title=node_group_title(document, part[0], level_name, key),
        ))

    row_width = min(
        options.max_row_width,
        max(c.width for c in children) * min(len(children), 3) + options.group_gap * 2,
    )
    return _pack_children(
        children,
        level_name=level_name,
        key=key,
        level_index=level_index,
        group_id=group_id,
        document=document,
        sample_nodes=nodes,
        options=options,
        row_width=row_width,
    )


def _layout_recursive(
    document: EasyFindDocument,
    nodes: list[EasyFindNode],
    levels: tuple[str, ...],
    depth: int,
    id_prefix: str,
    options: EasyFindLayoutOptions,
) -> _LayoutNode:
    level_name = levels[depth]
    buckets = _split_nodes(document, nodes, level_name)

    if depth == len(levels) - 1:
        children = [
            _layout_leaf(
                document,
                bucket_nodes,
                level_name=level_name,
                key=key,
                level_index=depth,
                group_id=f"{id_prefix}/{level_name}/{key}",
                options=options,
            )
            for key in sorted(buckets, key=lambda k: group_key_sort_order(level_name, k))
            for bucket_nodes in [buckets[key]]
        ]
        if len(children) == 1:
            return children[0]
        only_key = sorted(buckets, key=lambda k: group_key_sort_order(level_name, k))[0]
        return _pack_children(
            children,
            level_name=level_name,
            key=only_key,
            level_index=depth,
            group_id=f"{id_prefix}/{level_name}",
            document=document,
            sample_nodes=buckets[only_key],
            options=options,
            row_width=options.max_row_width,
        )

    children = []
    for key in sorted(buckets, key=lambda k: group_key_sort_order(level_name, k)):
        child_nodes = buckets[key]
        child = _layout_recursive(
            document,
            child_nodes,
            levels,
            depth + 1,
            f"{id_prefix}/{level_name}/{key}",
            options,
        )
        children.append(child)

    if len(children) == 1:
        child = children[0]
        return _pack_children(
            [child],
            level_name=level_name,
            key=next(iter(buckets)),
            level_index=depth,
            group_id=f"{id_prefix}/{level_name}/{next(iter(buckets))}",
            document=document,
            sample_nodes=next(iter(buckets.values())),
            options=options,
            row_width=options.max_row_width,
        )

    first_key = sorted(buckets, key=lambda k: group_key_sort_order(level_name, k))[0]
    return _pack_children(
        children,
        level_name=level_name,
        key=first_key,
        level_index=depth,
        group_id=f"{id_prefix}/{level_name}",
        document=document,
        sample_nodes=buckets[first_key],
        options=options,
        row_width=options.max_row_width,
    )


def _flatten(
    node: _LayoutNode,
    offset_x: float,
    offset_y: float,
) -> tuple[list[EasyFindGroupPlacement], dict[str, EasyFindNodePlacement]]:
    groups: list[EasyFindGroupPlacement] = []
    nodes: dict[str, EasyFindNodePlacement] = {}

    abs_x = offset_x + node.local_x
    abs_y = offset_y + node.local_y

    child_ids = tuple(c.group_id for c in node.children)
    groups.append(EasyFindGroupPlacement(
        group_id=node.group_id,
        title=node.title,
        level=node.level_index,
        accent_rgb=node.accent_rgb,
        x=abs_x,
        y=abs_y,
        width=node.width,
        height=node.height,
        node_ids=node.node_ids if not node.children else (),
        child_group_ids=child_ids,
    ))

    for nid, placement in node.node_placements.items():
        nodes[nid] = EasyFindNodePlacement(
            node_id=nid,
            x=abs_x + placement.x,
            y=abs_y + placement.y,
            width=placement.width,
            height=placement.height,
        )

    for child in node.children:
        child_groups, child_nodes = _flatten(child, abs_x, abs_y)
        groups.extend(child_groups)
        nodes.update(child_nodes)

    return groups, nodes


def _pack_roots_compact(roots: list[_LayoutNode], options: EasyFindLayoutOptions) -> None:
    """Place top-level groups in a grid that minimizes extreme aspect ratio."""
    if not roots:
        return
    gap = options.group_gap
    count = len(roots)
    best_cols = 1
    best_score = float("inf")
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        col_widths = [0.0] * cols
        row_heights = [0.0] * rows
        for index, root in enumerate(roots):
            row = index // cols
            col = index % cols
            col_widths[col] = max(col_widths[col], root.width)
            row_heights[row] = max(row_heights[row], root.height)
        total_w = sum(col_widths) + gap * max(0, cols - 1)
        total_h = sum(row_heights) + gap * max(0, rows - 1)
        score = max(total_w, total_h) + abs(total_w - total_h) * 0.35
        if score < best_score:
            best_score = score
            best_cols = cols

    cols = best_cols
    rows = math.ceil(count / cols)
    col_widths = [0.0] * cols
    row_heights = [0.0] * rows
    for index, root in enumerate(roots):
        row = index // cols
        col = index % cols
        col_widths[col] = max(col_widths[col], root.width)
        row_heights[row] = max(row_heights[row], root.height)

    for index, root in enumerate(roots):
        row = index // cols
        col = index % cols
        root.local_x = sum(col_widths[:col]) + gap * col
        root.local_y = sum(row_heights[:row]) + gap * row


CLUSTER_TILE_WIDTH = 220.0
CLUSTER_TILE_HEIGHT = 140.0
CLUSTER_TILE_GAP = 32.0


def _overview_columns(count: int) -> int:
    if count <= 1:
        return 1
    return max(1, int(math.ceil(math.sqrt(count * 1.15))))


def build_cluster_overview_layout(
    document: EasyFindDocument,
    *,
    filters: EasyFindCanvasFilters,
    index: object | None = None,
) -> EasyFindCanvasLayout:
    """Place top-level cluster portals only (HPA-style coarse map)."""
    visible_nodes = filter_nodes(document, filters, index=index)
    if not visible_nodes:
        return EasyFindCanvasLayout(
            groups=(),
            sections=(),
            nodes={},
            bounds=EasyFindRect(0.0, 0.0, 800.0, 400.0),
            empty=True,
            clusters=(),
        )

    group_by = filters.group_by if filters.group_by in GROUPING_LEVELS else "color"
    levels = GROUPING_LEVELS[group_by]
    top_name = levels[0]
    top_buckets = _split_nodes(document, visible_nodes, top_name)

    keys = sorted(top_buckets, key=lambda k: group_key_sort_order(top_name, k))
    cols = _overview_columns(len(keys))
    clusters: list[EasyFindClusterPlacement] = []
    max_right = 0.0
    max_bottom = 0.0

    for index, key in enumerate(keys):
        bucket_nodes = top_buckets[key]
        col = index % cols
        row = index // cols
        x = col * (CLUSTER_TILE_WIDTH + CLUSTER_TILE_GAP)
        y = row * (CLUSTER_TILE_HEIGHT + CLUSTER_TILE_GAP)
        title = node_group_title(document, bucket_nodes[0], top_name, key)
        clusters.append(EasyFindClusterPlacement(
            cluster_id=f"cluster/{top_name}/{key}",
            title=title,
            level_name=top_name,
            key=key,
            level_index=0,
            accent_rgb=_accent(top_name, key),
            x=x,
            y=y,
            width=CLUSTER_TILE_WIDTH,
            height=CLUSTER_TILE_HEIGHT,
            node_count=len(bucket_nodes),
            node_ids=tuple(node.node_id for node in bucket_nodes),
        ))
        max_right = max(max_right, x + CLUSTER_TILE_WIDTH)
        max_bottom = max(max_bottom, y + CLUSTER_TILE_HEIGHT)

    bounds = EasyFindRect(
        0.0,
        0.0,
        max(max_right, 800.0),
        max(max_bottom, 400.0),
    )
    return EasyFindCanvasLayout(
        groups=(),
        sections=(),
        nodes={},
        bounds=bounds,
        empty=False,
        clusters=tuple(clusters),
    )


def build_cluster_detail_layout(
    document: EasyFindDocument,
    *,
    filters: EasyFindCanvasFilters,
    cluster_id: str,
    member_ids: frozenset[str],
    options: EasyFindLayoutOptions | None = None,
    index: object | None = None,
) -> EasyFindCanvasLayout:
    """Layout tiles for one opened cluster (inner grouping levels only)."""
    opts = options or EasyFindLayoutOptions(group_by=filters.group_by)
    members = [
        node for node in filter_nodes(document, filters, index=index)
        if node.node_id in member_ids
    ]
    if not members:
        return EasyFindCanvasLayout(
            groups=(),
            sections=(),
            nodes={},
            bounds=EasyFindRect(0.0, 0.0, 1.0, 1.0),
            empty=True,
            clusters=(),
        )

    group_by = filters.group_by if filters.group_by in GROUPING_LEVELS else "color"
    levels = GROUPING_LEVELS[group_by]

    if len(levels) == 1:
        root = _layout_leaf(
            document,
            members,
            level_name=levels[0],
            key="all",
            level_index=0,
            group_id=cluster_id,
            options=opts,
        )
    else:
        root = _layout_recursive(
            document,
            members,
            levels,
            1,
            cluster_id,
            opts,
        )

    groups, nodes = _flatten(root, 0.0, 0.0)
    bounds = EasyFindRect(0.0, 0.0, max(root.width, 1.0), max(root.height, 1.0))
    return EasyFindCanvasLayout(
        groups=tuple(groups),
        sections=(),
        nodes=nodes,
        bounds=bounds,
        empty=not nodes,
        clusters=(),
    )


def build_source_level_canvas_layout(
    document: EasyFindDocument,
    *,
    filters: EasyFindCanvasFilters,
    options: EasyFindLayoutOptions | None = None,
    index: object | None = None,
) -> EasyFindCanvasLayout:
    """Compute deterministic group and node placements."""
    opts = options or EasyFindLayoutOptions(group_by=filters.group_by)
    visible_nodes = filter_nodes(document, filters, index=index)
    if not visible_nodes:
        return EasyFindCanvasLayout(
            groups=(),
            sections=(),
            nodes={},
            bounds=EasyFindRect(0.0, 0.0, 800.0, 400.0),
            empty=True,
        )

    group_by = filters.group_by if filters.group_by in GROUPING_LEVELS else "color"
    levels = GROUPING_LEVELS[group_by]
    top_name = levels[0]
    top_buckets = _split_nodes(document, visible_nodes, top_name)

    roots: list[_LayoutNode] = []
    for key in sorted(top_buckets, key=lambda k: group_key_sort_order(top_name, k)):
        bucket_nodes = top_buckets[key]
        if len(levels) == 1:
            roots.append(_layout_leaf(
                document,
                bucket_nodes,
                level_name=top_name,
                key=key,
                level_index=0,
                group_id=f"root/{top_name}/{key}",
                options=opts,
            ))
        else:
            inner = _layout_recursive(
                document,
                bucket_nodes,
                levels,
                1,
                f"root/{top_name}/{key}",
                opts,
            )
            roots.append(_pack_children(
                [inner],
                level_name=top_name,
                key=key,
                level_index=0,
                group_id=f"root/{top_name}/{key}",
                document=document,
                sample_nodes=bucket_nodes,
                options=opts,
                row_width=opts.max_row_width,
            ))

    _pack_roots_compact(roots, opts)

    max_right = 0.0
    max_bottom = 0.0
    all_groups: list[EasyFindGroupPlacement] = []
    all_nodes: dict[str, EasyFindNodePlacement] = {}

    for root in roots:
        groups, nodes = _flatten(root, 0.0, 0.0)
        all_groups.extend(groups)
        all_nodes.update(nodes)
        max_right = max(max_right, root.local_x + root.width)
        max_bottom = max(max_bottom, root.local_y + root.height)

    group_by_id = {g.group_id: g for g in all_groups}

    def _descendant_node_count(group_id: str) -> int:
        group = group_by_id[group_id]
        if group.node_ids:
            return len(group.node_ids)
        return sum(_descendant_node_count(cid) for cid in group.child_group_ids)

    sections = tuple(
        EasyFindSectionPlacement(
            section_id=g.group_id,
            title=g.title,
            count=_descendant_node_count(g.group_id),
            x=g.x,
            y=g.y,
            width=g.width,
            height=g.height,
            node_ids=g.node_ids,
        )
        for g in all_groups
        if g.level == 0
    )

    bounds = EasyFindRect(0.0, 0.0, max(max_right, 800.0), max(max_bottom, 400.0))
    return EasyFindCanvasLayout(
        groups=tuple(all_groups),
        sections=sections,
        nodes=all_nodes,
        bounds=bounds,
        empty=False,
    )
