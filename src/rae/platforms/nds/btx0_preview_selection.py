"""Choose which BTX0 dictionary entry to decode for thumbnails and previews."""
from __future__ import annotations

import re
from pathlib import Path

from .nitro_textures import parse_tex0_manifest
from .scanner import Asset

_NUMBERED_SLOT = re.compile(r"^(.+?)\.(\d+)$")

# Pokémon-style overworld NPC sheets: .1 is usually up/back, .2 down (toward camera).
_THUMB_DIRECTION_PRIORITY = (2, 4, 3, 1)


def btx0_texture_entry_names(data: bytes) -> list[str]:
    manifest = parse_tex0_manifest(data)
    if not manifest:
        return []
    return [tex.name for tex in manifest.textures if tex.name]


def asset_file_index(virtual_path: str) -> int | None:
    path = virtual_path.replace("\\", "/")
    match = re.search(r"file_(\d+)", path, re.IGNORECASE)
    if match:
        return int(match.group(1))
    nums = re.findall(r"\d+", Path(path).name)
    return int(nums[-1]) if nums else None


def _pick_from_numbered_group(entries: list[tuple[int, str]]) -> str:
    num_to_name = {num: name for num, name in entries}
    for preferred in _THUMB_DIRECTION_PRIORITY:
        if preferred in num_to_name:
            return num_to_name[preferred]
    return sorted(entries, key=lambda row: row[0])[0][1]


def choose_btx0_thumbnail_texture_name(asset: Asset) -> str | None:
    """Pick one BTX0 texture dictionary name for thumbnails / EasyFind tiles."""
    if getattr(asset, "is_texture_slot", False) and asset.texture_slot:
        return asset.texture_slot

    names = btx0_texture_entry_names(asset.data)
    if not names:
        return None
    if len(names) == 1:
        return names[0]

    groups: dict[str, list[tuple[int, str]]] = {}
    for name in names:
        match = _NUMBERED_SLOT.match(name)
        if match:
            prefix = match.group(1)
            groups.setdefault(prefix, []).append((int(match.group(2)), name))

    if len(groups) == 1:
        _prefix, entries = next(iter(groups.items()))
        return _pick_from_numbered_group(entries)

    file_index = asset_file_index(asset.virtual_path)
    path_l = asset.virtual_path.casefold()
    stem = Path(asset.virtual_path).name.split(".")[0].casefold()

    if file_index is not None:
        for token in (f"{file_index:04d}", f"{file_index:03d}", str(file_index)):
            for name in names:
                if token in name.casefold():
                    return name
        if "file_" in path_l and file_index < len(names):
            return names[file_index]

    for name in names:
        if name.casefold() == stem:
            return name

    if groups:
        largest = max(groups.values(), key=len)
        if len(largest) >= 2:
            return _pick_from_numbered_group(largest)

    manifest = parse_tex0_manifest(asset.data)
    if manifest and manifest.textures:
        return max(manifest.textures, key=lambda tex: tex.width * tex.height).name
    return names[0]
