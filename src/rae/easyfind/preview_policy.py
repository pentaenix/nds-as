"""EasyFind preview bake policy — visual-first builds."""
from __future__ import annotations

from ..scanner import Asset
from .build_index import classify_asset_magic
from .models import EasyFindNode

SKIP_PREVIEW_NODE_KINDS = frozenset({"audio", "animation"})
SKIP_PREVIEW_MAGICS = frozenset({"RLCN"})

ALWAYS_ATTEMPT_NODE_KINDS = frozenset({
    "model",
    "texture_slot",
    "texture_archive",
    "image_or_sprite_source",
    "archive",
    "unknown",
})


def should_bake_preview(node: EasyFindNode, asset: Asset | None) -> bool:
    """Return True when this node should receive a thumbnail bake attempt."""
    magic = (asset.magic if asset is not None else str(node.metadata.get("magic", ""))).upper()
    if magic in SKIP_PREVIEW_MAGICS:
        return False
    if node.node_kind in SKIP_PREVIEW_NODE_KINDS:
        return False
    if node.node_kind in ALWAYS_ATTEMPT_NODE_KINDS:
        return True
    kind = classify_asset_magic(magic, asset=asset)
    if kind in SKIP_PREVIEW_NODE_KINDS:
        return False
    return True


def preview_skipped_reason(node: EasyFindNode, asset: Asset | None) -> str | None:
    if should_bake_preview(node, asset):
        return None
    if node.node_kind in SKIP_PREVIEW_NODE_KINDS:
        return f"skipped:{node.node_kind}"
    magic = (asset.magic if asset is not None else str(node.metadata.get("magic", ""))).upper()
    if magic in SKIP_PREVIEW_MAGICS:
        return f"skipped:magic:{magic}"
    return "skipped:policy"
