"""Expand multi-entry BTX0 archives into browsable texture-slot assets."""
from __future__ import annotations

import re

from .nitro_textures import parse_tex0_manifest
from .scanner import Asset

_MIN_NAMED_SLOTS = 2
_SLOT_GROUP_RE = re.compile(r"^(.+?)\.(\d+)$")


def texture_slot_group_name(slot_name: str, *, archive_stem: str) -> str:
    match = _SLOT_GROUP_RE.match(slot_name.strip())
    if match:
        return match.group(1)
    return archive_stem or slot_name


def make_texture_slot_asset(parent: Asset, slot_name: str) -> Asset:
    safe_slot = slot_name.replace("/", "_")
    return Asset(
        asset_id=f"{parent.asset_id}::slot::{safe_slot}",
        virtual_path=f"{parent.virtual_path}#{slot_name}",
        kind="Texture slot",
        magic="BTX0",
        extension=parent.extension,
        data=parent.data,
        original_data=parent.original_data,
        rom_file_id=parent.rom_file_id,
        rom_offset=parent.rom_offset,
        compressed=parent.compressed,
        container_chain=parent.container_chain,
        carved=parent.carved,
        carved_offset=parent.carved_offset,
        mapping_category=parent.mapping_category,
        mapping_label=parent.mapping_label,
        mapping_confidence=parent.mapping_confidence,
        parent_asset_id=parent.asset_id,
        texture_slot=slot_name,
        is_texture_slot=True,
    )


def expand_btx0_texture_slots(assets: list[Asset]) -> list[Asset]:
    """Add one virtual asset per named BTX0 dictionary entry (e.g. princess.1 … princess.N)."""
    expanded: list[Asset] = []
    for asset in assets:
        if asset.is_texture_slot:
            expanded.append(asset)
            continue
        if asset.magic != "BTX0":
            expanded.append(asset)
            continue
        manifest = parse_tex0_manifest(asset.data)
        slot_names = [tex.name for tex in manifest.textures if tex.name] if manifest else []
        if len(slot_names) < _MIN_NAMED_SLOTS:
            expanded.append(asset)
            continue
        expanded.append(asset)
        for slot_name in slot_names:
            expanded.append(make_texture_slot_asset(asset, slot_name))
    return expanded


def physical_assets(assets: list[Asset]) -> list[Asset]:
    """ROM/session payloads only — omit virtual texture-slot rows."""
    return [asset for asset in assets if not asset.is_texture_slot]
