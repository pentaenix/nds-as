"""Parse Gen 4 mapname.bin tables."""
from __future__ import annotations

MAPNAME_RECORD_SIZE = 16


def parse_mapname_bin(data: bytes) -> list[str]:
    """Return raw 16-byte map code strings from mapname.bin."""
    if not data:
        return []
    names: list[str] = []
    offset = 0
    while offset + MAPNAME_RECORD_SIZE <= len(data):
        chunk = data[offset:offset + MAPNAME_RECORD_SIZE]
        offset += MAPNAME_RECORD_SIZE
        raw = chunk.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
        if not raw:
            if not names:
                continue
            break
        names.append(raw)
    return names
