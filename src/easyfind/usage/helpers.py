"""Shared helpers for usage extraction from scanned ROM assets."""
from __future__ import annotations

import re
from pathlib import PurePosixPath

from ...core.mapping import GameMapping, normalize_path
from ...scanner import Asset
from ..models import EasyFindDocument, EasyFindNode

_FILE_INDEX_RE = re.compile(r"file_(\d+)", re.IGNORECASE)


def usage_archives_for_mapping(mapping: GameMapping | None) -> dict[str, str]:
    if mapping is None:
        return {}
    return dict(getattr(mapping, "usage_archives", {}) or {})


def place_name_overlay(mapping: GameMapping | None) -> dict[str, str]:
    if mapping is None:
        return {}
    raw = getattr(mapping, "place_names", None)
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def find_asset_by_archive_path(assets: list[Asset], archive_path: str) -> Asset | None:
    """Find a top-level scanned asset whose virtual path matches an archive path."""
    if not archive_path:
        return None
    target = normalize_path(archive_path).strip("/")
    best: tuple[int, Asset] | None = None
    for asset in assets:
        path = normalize_path(asset.virtual_path).strip("/")
        if path == target:
            return asset
        if path.endswith("/" + target.split("/")[-1]):
            score = len(path)
            if best is None or score < best[0]:
                best = (score, asset)
    return best[1] if best else None


def narc_child_index_from_path(virtual_path: str) -> int | None:
    name = PurePosixPath(virtual_path.replace("\\", "/")).name
    match = _FILE_INDEX_RE.search(name)
    if match:
        return int(match.group(1))
    stem = name.split(".", 1)[0]
    if stem.isdigit():
        return int(stem)
    return None


def index_build_model_nodes(
    document: EasyFindDocument,
    *,
    archive_path: str,
) -> dict[int, str]:
    """Map build_model.narc entry index -> EasyFind node_id."""
    archive_key = archive_path.casefold().strip("/")
    by_index: dict[int, str] = {}
    for node in document.nodes:
        if node.node_kind != "model":
            continue
        if not node.asset_refs:
            continue
        asset_id = node.asset_refs[0].asset_id
        asset = next((a for a in document.assets if a.asset_id == asset_id), None)
        if asset is None:
            continue
        path = asset.virtual_path.replace("\\", "/").casefold()
        if archive_key not in path:
            continue
        idx = narc_child_index_from_path(asset.virtual_path)
        if idx is None:
            continue
        by_index.setdefault(idx, node.node_id)
    return by_index


def node_id_for_asset(document: EasyFindDocument, asset_id: str) -> str | None:
    node_id = f"asset:{asset_id}"
    if any(n.node_id == node_id for n in document.nodes):
        return node_id
    return None
