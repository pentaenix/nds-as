"""Display names and region buckets for in-game map codes."""
from __future__ import annotations

import re

REGION_GROUPS: tuple[str, ...] = (
    "Towns",
    "Routes",
    "Interiors",
    "Dungeons",
    "Special",
    "Unknown",
)

_CODE_PREFIX_TO_GROUP: dict[str, str] = {
    "T": "Towns",
    "R": "Routes",
    "C": "Interiors",
    "D": "Dungeons",
    "L": "Dungeons",
    "W": "Special",
    "P": "Special",
}


def region_group_for_code(raw_name: str) -> str:
    """Map internal map codes (T20R0101, etc.) to a coarse UI region bucket."""
    code = raw_name.strip()
    if not code:
        return "Unknown"
    prefix = code[0].upper()
    return _CODE_PREFIX_TO_GROUP.get(prefix, "Unknown")


def format_code_name(raw_name: str) -> str:
    """Turn a raw mapname.bin entry into a slightly more readable label."""
    code = raw_name.strip("\x00").strip()
    if not code:
        return ""
    match = re.match(
        r"^([CTDLRWP])(\d{2})(PC|FS|GYM|R)?(\d{2})?(\d{2})?$",
        code,
    )
    if match is None:
        return code
    type_char = match.group(1)
    loc_id = match.group(2)
    subtype = match.group(3) or ""
    sub_id = match.group(4) or ""
    sub_id2 = match.group(5) or ""
    type_names = {
        "T": "Town",
        "C": "Interior",
        "D": "Dungeon",
        "L": "Dungeon",
        "R": "Route",
        "W": "Special",
        "P": "Special",
    }
    parts = [type_names.get(type_char, type_char), loc_id]
    if subtype:
        parts.append(subtype)
    if sub_id:
        parts.append(sub_id)
    if sub_id2:
        parts.append(sub_id2)
    return " ".join(parts)


def display_map_name(
    *,
    map_index: int,
    raw_name: str,
    overlay: dict[str, str] | None = None,
) -> str:
    """Prefer community overlay, then formatted code, then Map #index."""
    raw = raw_name.strip("\x00").strip()
    if overlay:
        for key in (raw, f"map:{map_index:03d}", str(map_index)):
            if key in overlay:
                return overlay[key]
    if raw:
        formatted = format_code_name(raw)
        if formatted and formatted != raw:
            return f"{formatted} ({raw})"
        return raw
    return f"Map #{map_index}"
