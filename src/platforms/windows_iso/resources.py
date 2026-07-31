"""Parsers for Marine Park Empire's indexed text resource tables."""
from __future__ import annotations

from dataclasses import dataclass
import re
import struct


@dataclass(frozen=True, slots=True)
class ModelBinding:
    animation_stems: tuple[str, ...] = ()
    simple_shadow: str | None = None
    detail_shadow: str | None = None


_PROPERTY = re.compile(r"^\s*([A-Z0-9_]+)\s*:\s*(.*?)\s*$", re.IGNORECASE | re.MULTILINE)


def indexed_text_blocks(data: bytes) -> dict[str, str]:
    """Decode the game's 34-byte-key/u32-offset resource container."""
    if len(data) < 4:
        return {}
    count = struct.unpack_from("<I", data, 0)[0]
    table_end = 4 + count * 38
    if not count or count > 100_000 or table_end > len(data):
        return {}
    rows: list[tuple[str, int]] = []
    for index in range(count):
        offset = 4 + index * 38
        key = data[offset:offset + 34].split(b"\0", 1)[0].decode("cp1252", errors="replace").strip()
        pointer = struct.unpack_from("<I", data, offset + 34)[0]
        if not key or pointer < table_end or pointer > len(data):
            return {}
        rows.append((key.upper(), pointer))
    result: dict[str, str] = {}
    for index, (key, start) in enumerate(rows):
        end = rows[index + 1][1] if index + 1 < len(rows) else len(data)
        if end < start:
            return {}
        result[key] = data[start:end].decode("cp1252", errors="replace").strip("\0\r\n ")
    return result


def model_bindings(model_resource: bytes, animation_group_resource: bytes) -> dict[str, ModelBinding]:
    models = indexed_text_blocks(model_resource)
    groups = indexed_text_blocks(animation_group_resource)
    result: dict[str, ModelBinding] = {}
    for model_name, text in models.items():
        values: dict[str, list[str]] = {}
        for match in _PROPERTY.finditer(text):
            values.setdefault(match.group(1).upper(), []).append(match.group(2).strip())
        animation_names: list[str] = []
        for value in values.get("AM2", []):
            if value and value.casefold() not in {name.casefold() for name in animation_names}:
                animation_names.append(value)
        for group_name in values.get("ANIM_GROUP", []):
            group = groups.get(group_name.upper(), "")
            for match in _PROPERTY.finditer(group):
                if match.group(1).upper() == "AM2_NAME":
                    value = match.group(2).strip()
                    if value and value.casefold() not in {name.casefold() for name in animation_names}:
                        animation_names.append(value)
        result[model_name] = ModelBinding(
            animation_stems=tuple(animation_names),
            simple_shadow=(values.get("SIMPLE_SHADOW") or [None])[0],
            detail_shadow=(values.get("DETAIL_SHADOW") or [None])[0],
        )
    return result
