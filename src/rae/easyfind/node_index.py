"""Precomputed EasyFind node lookups for O(1) bucket membership."""
from __future__ import annotations

from dataclasses import dataclass

from .canvas_filters import node_color_bucket, node_sort_key
from .format import BUCKET_LOOKUP_VERSION
from .models import EasyFindDocument, EasyFindNode
from .validation import EasyFindCorruptError

_LOOKUP_MAP_KEYS = (
    "by_primary_bucket",
    "by_secondary_bucket",
    "by_node_kind",
    "by_location_id",
    "by_location_group",
)


@dataclass(frozen=True)
class EasyFindNodeIndex:
    nodes_by_id: dict[str, EasyFindNode]
    by_primary_bucket: dict[str, frozenset[str]]
    by_secondary_bucket: dict[str, frozenset[str]]
    by_node_kind: dict[str, frozenset[str]]
    by_location_id: dict[str, frozenset[str]]
    by_location_group: dict[str, frozenset[str]]


def _build_bucket_lookup_index(document: EasyFindDocument) -> EasyFindNodeIndex:
    """Bake-time only: derive lookup tables from nodes and color signatures."""
    nodes_by_id = {node.node_id: node for node in document.nodes}
    signatures_by_node = {sig.node_id: sig for sig in document.color_signatures}
    location_groups = {loc.location_id: loc.group for loc in document.locations}

    primary: dict[str, set[str]] = {}
    secondary: dict[str, set[str]] = {}
    by_kind: dict[str, set[str]] = {}
    by_location: dict[str, set[str]] = {}
    by_region: dict[str, set[str]] = {}

    for node in document.nodes:
        by_kind.setdefault(node.node_kind, set()).add(node.node_id)
        signature = signatures_by_node.get(node.node_id)
        if signature is None:
            primary.setdefault(node_color_bucket(document, node), set()).add(node.node_id)
        else:
            primary.setdefault(signature.dominant_bucket, set()).add(node.node_id)
            for bucket in signature.secondary_buckets:
                secondary.setdefault(bucket, set()).add(node.node_id)

    for tag in document.asset_tags:
        if not tag.location_id:
            continue
        by_location.setdefault(tag.location_id, set()).add(tag.node_id)
        group = location_groups.get(tag.location_id, "Unknown")
        by_region.setdefault(group, set()).add(tag.node_id)

    return EasyFindNodeIndex(
        nodes_by_id=nodes_by_id,
        by_primary_bucket={key: frozenset(ids) for key, ids in primary.items()},
        by_secondary_bucket={key: frozenset(ids) for key, ids in secondary.items()},
        by_node_kind={key: frozenset(ids) for key, ids in by_kind.items()},
        by_location_id={key: frozenset(ids) for key, ids in by_location.items()},
        by_location_group={key: frozenset(ids) for key, ids in by_region.items()},
    )


def bucket_lookup_to_dict(index: EasyFindNodeIndex) -> dict[str, object]:
    """Serialize lookup tables for storage inside the .easyfind archive."""

    def _sorted_bucket_map(data: dict[str, frozenset[str]]) -> dict[str, list[str]]:
        return {key: sorted(data[key]) for key in sorted(data)}

    return {
        "version": BUCKET_LOOKUP_VERSION,
        "by_primary_bucket": _sorted_bucket_map(index.by_primary_bucket),
        "by_secondary_bucket": _sorted_bucket_map(index.by_secondary_bucket),
        "by_node_kind": _sorted_bucket_map(index.by_node_kind),
        "by_location_id": _sorted_bucket_map(index.by_location_id),
        "by_location_group": _sorted_bucket_map(index.by_location_group),
    }


def _read_bucket_map(raw: object) -> dict[str, frozenset[str]]:
    if not isinstance(raw, dict):
        return {}
    result: dict[str, frozenset[str]] = {}
    for key, value in raw.items():
        if isinstance(value, list):
            result[str(key)] = frozenset(str(node_id) for node_id in value)
    return result


def node_index_from_bucket_lookup(
    lookup: dict[str, object],
    nodes: list[EasyFindNode],
) -> EasyFindNodeIndex:
    """Restore an index from signatures/bucket_lookup.json."""
    nodes_by_id = {node.node_id: node for node in nodes}
    return EasyFindNodeIndex(
        nodes_by_id=nodes_by_id,
        by_primary_bucket=_read_bucket_map(lookup.get("by_primary_bucket")),
        by_secondary_bucket=_read_bucket_map(lookup.get("by_secondary_bucket")),
        by_node_kind=_read_bucket_map(lookup.get("by_node_kind")),
        by_location_id=_read_bucket_map(lookup.get("by_location_id")),
        by_location_group=_read_bucket_map(lookup.get("by_location_group")),
    )


def validate_bucket_lookup(
    lookup: dict[str, object],
    node_ids: set[str],
) -> list[str]:
    """Validate baked lookup tables against the node index."""
    errors: list[str] = []
    version = lookup.get("version")
    if version != BUCKET_LOOKUP_VERSION:
        errors.append(
            f"Unsupported bucket_lookup version: {version!r} "
            f"(expected {BUCKET_LOOKUP_VERSION})"
        )

    for key in _LOOKUP_MAP_KEYS:
        if key not in lookup:
            errors.append(f"bucket_lookup missing required key: {key}")

    by_kind = _read_bucket_map(lookup.get("by_node_kind"))
    by_primary = _read_bucket_map(lookup.get("by_primary_bucket"))

    kind_members: set[str] = set()
    for bucket, members in by_kind.items():
        unknown = members - node_ids
        if unknown:
            errors.append(
                f"bucket_lookup by_node_kind[{bucket!r}] references unknown nodes: "
                f"{sorted(unknown)[:3]}"
            )
        kind_members |= set(members)

    primary_members: set[str] = set()
    for bucket, members in by_primary.items():
        unknown = members - node_ids
        if unknown:
            errors.append(
                f"bucket_lookup by_primary_bucket[{bucket!r}] references unknown nodes: "
                f"{sorted(unknown)[:3]}"
            )
        primary_members |= set(members)

    missing_kind = node_ids - kind_members
    if missing_kind:
        errors.append(
            f"bucket_lookup by_node_kind is missing {len(missing_kind)} node(s)"
        )

    missing_primary = node_ids - primary_members
    if missing_primary:
        errors.append(
            f"bucket_lookup by_primary_bucket is missing {len(missing_primary)} node(s)"
        )

    for map_key in ("by_secondary_bucket", "by_location_id", "by_location_group"):
        by_map = _read_bucket_map(lookup.get(map_key))
        for bucket, members in by_map.items():
            unknown = members - node_ids
            if unknown:
                errors.append(
                    f"bucket_lookup {map_key}[{bucket!r}] references unknown nodes: "
                    f"{sorted(unknown)[:3]}"
                )

    return errors


def load_node_index(document: EasyFindDocument) -> EasyFindNodeIndex:
    """Load the baked lookup tables from a loaded EasyFind document."""
    if not document.bucket_lookup:
        raise EasyFindCorruptError(
            "EasyFind file is missing signatures/bucket_lookup.json. Rebuild EasyFind."
        )
    errors = validate_bucket_lookup(
        document.bucket_lookup,
        {node.node_id for node in document.nodes},
    )
    if errors:
        raise EasyFindCorruptError(
            "Invalid bucket_lookup tables: " + errors[0]
        )
    return node_index_from_bucket_lookup(document.bucket_lookup, document.nodes)


def attach_bucket_lookup(document: EasyFindDocument) -> EasyFindDocument:
    """Bake lookup tables after preview/color signature generation."""
    index = _build_bucket_lookup_index(document)
    document.bucket_lookup = bucket_lookup_to_dict(index)
    document.build_info = dict(document.build_info)
    document.build_info["bucket_lookup_version"] = BUCKET_LOOKUP_VERSION
    document.build_info["bucket_lookup_buckets"] = {
        "primary": len(index.by_primary_bucket),
        "secondary": len(index.by_secondary_bucket),
        "node_kind": len(index.by_node_kind),
        "location_id": len(index.by_location_id),
        "location_group": len(index.by_location_group),
    }
    if document.manifest.capabilities:
        caps = dict(document.manifest.capabilities)
        caps["contains_bucket_lookup"] = True
        document.manifest.capabilities = caps
    return document


def nodes_for_primary_colors(index: EasyFindNodeIndex, colors: frozenset[str]) -> frozenset[str]:
    if not colors:
        return frozenset()
    merged: set[str] = set()
    for color in colors:
        merged |= set(index.by_primary_bucket.get(color, ()))
    return frozenset(merged)


def nodes_for_secondary_colors(index: EasyFindNodeIndex, colors: frozenset[str]) -> frozenset[str]:
    if not colors:
        return frozenset()
    merged: set[str] = set()
    for color in colors:
        merged |= set(index.by_secondary_bucket.get(color, ()))
    return frozenset(merged)


def nodes_for_node_kind(index: EasyFindNodeIndex, node_kind: str) -> frozenset[str]:
    if not node_kind:
        return frozenset()
    return frozenset(index.by_node_kind.get(node_kind, ()))


def nodes_for_locations(index: EasyFindNodeIndex, location_ids: frozenset[str]) -> frozenset[str]:
    if not location_ids:
        return frozenset()
    merged: set[str] = set()
    for location_id in location_ids:
        merged |= set(index.by_location_id.get(location_id, ()))
    return frozenset(merged)


def nodes_for_location_groups(index: EasyFindNodeIndex, groups: frozenset[str]) -> frozenset[str]:
    if not groups:
        return frozenset()
    merged: set[str] = set()
    for group in groups:
        merged |= set(index.by_location_group.get(group, ()))
    return frozenset(merged)


def materialize_nodes(
    document: EasyFindDocument,
    index: EasyFindNodeIndex,
    node_ids: frozenset[str],
) -> list[EasyFindNode]:
    nodes = [index.nodes_by_id[node_id] for node_id in node_ids if node_id in index.nodes_by_id]
    nodes.sort(key=lambda node: node_sort_key(document, node))
    return nodes
