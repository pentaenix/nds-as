"""Nitro NameList parsing for TEX0 dictionaries."""
from __future__ import annotations

from ....core.util import read_u16le, read_u32le
from .types import NitroNameEntry, PaletteEntry, TextureEntry

def parse_namelist(data: bytes, off: int, fallback_entry_size: int) -> list[NitroNameEntry]:
    """Parse a Nitro NameList(T).

    Returns each entry's payload plus absolute offsets. The offsets matter for
    MDL0 material texture/palette pairings, whose MaterialIdxList payload points
    to a u8 list relative to the payload itself.
    """
    if off <= 0 or off + 16 > len(data):
        return []
    try:
        count = data[off + 1]
        total_size = read_u16le(data, off + 2)
        if count <= 0 or count > 2048 or total_size < 16 or off + total_size > len(data):
            return []

        # Normal Nitro Header layout:
        # 0x00 BBH, 0x04 unknown header (8 bytes), then count*u32 unknowns,
        # then HH for element size/data section length.
        element_size_pos = off + 12 + count * 4
        if element_size_pos + 4 > off + total_size:
            return []
        element_size = read_u16le(data, element_size_pos) or fallback_entry_size
        if element_size <= 0 or element_size > 4096:
            element_size = fallback_entry_size
        data_section_size = read_u16le(data, element_size_pos + 2)
        data_start = element_size_pos + 4
        if data_section_size <= 0 or data_section_size > total_size:
            data_section_size = count * element_size
        names_start = data_start + data_section_size

        # Some files report a larger data section than count*element_size. Names
        # are still the last 16*count bytes of the NameList, so recover.
        if names_start + 16 * count > off + total_size:
            names_start = off + total_size - 16 * count
        if names_start < data_start or names_start + 16 * count > len(data):
            return []

        entries: list[NitroNameEntry] = []
        for i in range(count):
            entry_start = data_start + i * element_size
            if entry_start + min(element_size, fallback_entry_size) > len(data):
                break
            raw_name = data[names_start + i * 16:names_start + (i + 1) * 16]
            name = decode_nitro_name(raw_name) or f"entry_{i:03d}"
            entries.append(NitroNameEntry(
                name=name,
                data=data[entry_start:entry_start + element_size],
                entry_offset=entry_start,
                list_offset=off,
            ))
        return entries
    except Exception:
        return []

def decode_nitro_name(raw: bytes) -> str | None:
    raw = raw.split(b"\0", 1)[0].strip()
    if not raw:
        return None
    try:
        return raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return raw.decode("shift_jis", errors="replace")

