"""PNG alpha analysis — port of pokemon-resort-page texture-alpha.mjs."""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from typing import BinaryIO

_PNG_SIG = bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])


def png_buffer_has_alpha_channel(buf: bytes) -> bool:
    if len(buf) < 26:
        return False
    if buf[0:4] != b"\x89PNG":
        return False
    color_type = buf[25]
    return color_type in (4, 6)


def is_png(buf: bytes) -> bool:
    return len(buf) >= 8 and buf[:8] == _PNG_SIG


@dataclass(frozen=True)
class PngAlphaDecode:
    width: int
    height: int
    alpha: bytes | None
    trns_present: bool


def decode_png_alpha(buf: bytes) -> PngAlphaDecode | None:
    off = 8
    width = 0
    height = 0
    bit_depth = 0
    color_type = 0
    interlace = 0
    idat: list[bytes] = []
    trns_present = False
    while off + 8 <= len(buf):
        chunk_len = struct.unpack_from(">I", buf, off)[0]
        chunk_type = buf[off + 4 : off + 8]
        data_start = off + 8
        if chunk_type == b"IHDR":
            width = struct.unpack_from(">I", buf, data_start)[0]
            height = struct.unpack_from(">I", buf, data_start + 4)[0]
            bit_depth = buf[data_start + 8]
            color_type = buf[data_start + 9]
            interlace = buf[data_start + 12]
        elif chunk_type == b"tRNS":
            trns_present = True
        elif chunk_type == b"IDAT":
            idat.append(buf[data_start : data_start + chunk_len])
        elif chunk_type == b"IEND":
            break
        off = data_start + chunk_len + 4

    has_alpha_channel = color_type in (4, 6)
    if not has_alpha_channel:
        return PngAlphaDecode(width=width, height=height, alpha=None, trns_present=trns_present)
    if bit_depth != 8 or interlace != 0 or not idat:
        return None

    channels = 4 if color_type == 6 else 2
    stride = width * channels
    try:
        raw = zlib.decompress(b"".join(idat))
    except zlib.error:
        return None
    if len(raw) < (stride + 1) * height:
        return None

    out = bytearray(height * stride)
    prev = bytearray(stride)
    p = 0
    for _y in range(height):
        filt = raw[p]
        p += 1
        cur = bytearray(stride)
        for x in range(stride):
            a = cur[x - channels] if x >= channels else 0
            b = prev[x]
            c = prev[x - channels] if x >= channels else 0
            v = raw[p + x]
            if filt == 1:
                v = (v + a) & 0xFF
            elif filt == 2:
                v = (v + b) & 0xFF
            elif filt == 3:
                v = (v + ((a + b) >> 1)) & 0xFF
            elif filt == 4:
                pp = a + b - c
                pa = abs(pp - a)
                pb = abs(pp - b)
                pc = abs(pp - c)
                v = (v + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 0xFF
            cur[x] = v
        out[_y * stride : (_y + 1) * stride] = cur
        prev = cur
        p += stride

    alpha_offset = channels - 1
    alpha = bytes(out[i * channels + alpha_offset] for i in range(width * height))
    return PngAlphaDecode(width=width, height=height, alpha=alpha, trns_present=trns_present)


def png_has_meaningful_transparency(
    buf: bytes,
    *,
    cutoff: float = 0.5,
    min_fraction: float = 0.005,
) -> bool:
    cutoff_byte = round(cutoff * 255)
    if len(buf) < 26 or not is_png(buf):
        return False
    decoded = decode_png_alpha(buf)
    if decoded is None:
        return png_buffer_has_alpha_channel(buf)
    if decoded.alpha is None:
        return decoded.trns_present
    total = len(decoded.alpha) or 1
    transparent = sum(1 for value in decoded.alpha if value < cutoff_byte)
    return (transparent / total) >= min_fraction


def texture_has_meaningful_alpha(
    texture_bytes: bytes | None,
    *,
    format_hint: str | None = None,
) -> bool:
    if not texture_bytes:
        return False
    if format_hint and format_hint.lower() in {"jpeg", "jpg"}:
        return False
    return png_has_meaningful_transparency(texture_bytes)


def texture_has_partial_alpha_channel(texture_bytes: bytes | None) -> bool:
    """True when any texel alpha is strictly between cutout and opaque."""
    if not texture_bytes or not is_png(texture_bytes):
        return False
    decoded = decode_png_alpha(texture_bytes)
    if decoded is None or decoded.alpha is None:
        return False
    return any(8 < value < 247 for value in decoded.alpha)
