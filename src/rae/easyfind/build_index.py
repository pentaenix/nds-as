"""Build EasyFind index documents from RAE assets."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..scanner import Asset
from .format import EASYFIND_FORMAT, EASYFIND_SCHEMA_VERSION
from .models import (
    EasyFindAssetRef,
    EasyFindDocument,
    EasyFindIdentity,
    EasyFindManifest,
    EasyFindNode,
    EasyFindNodeAssetRef,
    EasyFindQuickOpen,
    default_capabilities,
    default_quick_open_view,
    empty_counts,
    empty_source,
    quick_open_counts_from_manifest,
)

MODEL_MAGICS = frozenset({"BMD0"})
TEXTURE_ARCHIVE_MAGICS = frozenset({"BTX0"})
IMAGE_OR_SPRITE_MAGICS = frozenset({
    "RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR", "PNG",
})
AUDIO_MAGICS = frozenset({
    "SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM",
})
ANIMATION_MAGICS = frozenset({
    "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0",
})
ARCHIVE_MAGICS = frozenset({"NARC"})


def classify_asset_magic(magic: str) -> str:
    """Map a Nitro magic string to an EasyFind node kind."""
    upper = magic.upper()
    if upper in MODEL_MAGICS:
        return "model"
    if upper in TEXTURE_ARCHIVE_MAGICS:
        return "texture_archive"
    if upper in IMAGE_OR_SPRITE_MAGICS:
        return "image_or_sprite_source"
    if upper in AUDIO_MAGICS:
        return "audio"
    if upper in ANIMATION_MAGICS:
        return "animation"
    if upper in ARCHIVE_MAGICS:
        return "archive"
    return "unknown"


def asset_to_identity(asset: Asset) -> EasyFindIdentity:
    return EasyFindIdentity(
        asset_id=asset.asset_id,
        virtual_path=asset.virtual_path,
        magic=asset.magic,
        rom_file_id=asset.rom_file_id,
        rom_offset=asset.rom_offset,
        size=asset.size,
        container_chain=asset.container_chain,
    )


def asset_to_ref(asset: Asset) -> EasyFindAssetRef:
    return EasyFindAssetRef(
        asset_id=asset.asset_id,
        virtual_path=asset.virtual_path,
        kind=asset.kind,
        magic=asset.magic,
        extension=asset.extension,
        rom_file_id=asset.rom_file_id,
        rom_offset=asset.rom_offset,
        size=asset.size,
        original_size=asset.original_size,
        compressed=asset.compressed,
        container_chain=asset.container_chain,
        carved=asset.carved,
        carved_offset=asset.carved_offset,
        mapping_category=asset.mapping_category,
        mapping_label=asset.mapping_label,
        mapping_confidence=asset.mapping_confidence,
        identity=asset_to_identity(asset),
    )


def asset_to_node(asset: Asset) -> EasyFindNode:
    return EasyFindNode(
        node_id=f"asset:{asset.asset_id}",
        node_kind=classify_asset_magic(asset.magic),
        label=asset.virtual_path,
        asset_refs=[
            EasyFindNodeAssetRef(
                asset_id=asset.asset_id,
                sub_id=None,
                role="primary",
            )
        ],
        parent_node_id=None,
        child_node_ids=[],
        preview_ref=None,
        color_signature_ref=None,
        layout_ref=None,
        visibility="normal",
        metadata={
            "magic": asset.magic,
            "kind": asset.kind,
            "extension": asset.extension,
        },
    )


def create_easyfind_document(
    *,
    assets: list[Asset],
    rom_path: str | Path | None = None,
    platform: str = "nds",
    rom_title: str = "",
    rom_game_code: str = "",
    build_mode: str = "index",
) -> EasyFindDocument:
    """Create a complete EasyFind document from scanned assets."""
    now = datetime.now(timezone.utc).isoformat()
    build_id = str(uuid.uuid4())
    rom_name = Path(rom_path).name if rom_path else ""
    rom_path_note = str(rom_path or "")

    sorted_assets = sorted(assets, key=lambda a: a.asset_id)
    asset_refs = [asset_to_ref(asset) for asset in sorted_assets]
    nodes = sorted(
        [asset_to_node(asset) for asset in sorted_assets],
        key=lambda n: n.node_id,
    )

    counts = empty_counts()
    counts["assets"] = len(asset_refs)
    counts["nodes"] = len(nodes)

    source = empty_source(
        platform=platform,
        rom_name=rom_name,
        rom_path_note=rom_path_note,
        rom_title=rom_title,
        rom_game_code=rom_game_code,
        asset_count=len(asset_refs),
    )

    manifest = EasyFindManifest(
        format=EASYFIND_FORMAT,
        schema_version=EASYFIND_SCHEMA_VERSION,
        created_utc=now,
        updated_utc=now,
        source=source,
        counts=counts,
        capabilities=default_capabilities(),
        build={
            "builder": "rae",
            "build_id": build_id,
            "build_mode": build_mode,
            "started_utc": now,
            "completed_utc": now,
        },
    )

    quick_open = EasyFindQuickOpen(
        format=EASYFIND_FORMAT,
        schema_version=EASYFIND_SCHEMA_VERSION,
        source={
            "platform": platform,
            "rom_name": rom_name,
            "rom_title": rom_title,
            "rom_game_code": rom_game_code,
            "asset_count": len(asset_refs),
        },
        counts=quick_open_counts_from_manifest(counts),
        default_view=default_quick_open_view(),
        capabilities={
            "contains_previews": True,
            "contains_color_signatures": True,
            "contains_layout": True,
        },
    )

    return EasyFindDocument(
        manifest=manifest,
        quick_open=quick_open,
        assets=asset_refs,
        nodes=nodes,
        groups=[],
        layout={},
        locations=[],
        asset_tags=[],
        manual_merges=[],
        notes={},
        color_signatures=[],
        preview_refs=[],
        build_info={
            "builder": "rae",
            "build_id": build_id,
            "build_mode": build_mode,
            "asset_count": len(asset_refs),
            "node_count": len(nodes),
        },
        build_log="EasyFind index build completed.\n",
    )
