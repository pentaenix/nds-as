from __future__ import annotations

import struct
import zlib

from rae.platforms.nds.gltf.texture_alpha import (
    png_buffer_has_alpha_channel,
    png_has_meaningful_transparency,
)


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc_data = chunk_type + data
    import binascii

    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", binascii.crc32(crc_data) & 0xFFFFFFFF)


def _make_rgba_png(width: int, height: int, alpha_rows: list[bytes]) -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    raw = bytearray()
    for row in alpha_rows:
        raw.append(0)
        for x in range(width):
            raw.extend((255, 0, 0, row[x]))
    idat = zlib.compress(bytes(raw), level=9)
    return sig + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def test_opaque_rgba_png_is_not_meaningfully_transparent():
    rows = [bytes([255] * 4) for _ in range(4)]
    png = _make_rgba_png(4, 4, rows)
    assert png_buffer_has_alpha_channel(png)
    assert not png_has_meaningful_transparency(png)


def test_cutout_png_is_meaningfully_transparent():
    rows = [bytes([0, 255, 255, 0]) for _ in range(4)]
    png = _make_rgba_png(4, 4, rows)
    assert png_has_meaningful_transparency(png)
