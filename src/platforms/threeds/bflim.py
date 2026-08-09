"""BFLIM (CTR layout image) decoding for 3DS sprite/icon GARCs."""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .pica import decode_pica_texture, rgba_to_png

# BFLIM format byte -> internal PICA format index (pica.decode_pica_texture).
_BFLIM_FORMATS = {
    0: 7,   # L8
    1: 8,   # A8
    2: 9,   # LA4
    3: 5,   # LA8
    4: 6,   # HILO8
    5: 3,   # RGB565
    6: 1,   # RGB8
    7: 2,   # RGBA5551
    8: 4,   # RGBA4
    9: 0,   # RGBA8
    10: 12,  # ETC1
    11: 13,  # ETC1A4
    12: 10,  # L4
    13: 11,  # A4
}


class BflimError(ValueError):
    pass


@dataclass(slots=True)
class Bflim:
    width: int
    height: int
    format: int
    swizzle: int
    rgba: bytes

    def to_png(self) -> bytes:
        return rgba_to_png(self.rgba, self.width, self.height)


def is_bflim(data: bytes) -> bool:
    return len(data) > 0x28 and data[-0x28 : -0x28 + 4] == b"FLIM"


def decode_bflim(data: bytes) -> Bflim:
    if not is_bflim(data):
        raise BflimError("missing FLIM footer")
    imag = data[-0x14:]
    if imag[:4] != b"imag":
        raise BflimError("missing imag block")
    width, height, _alignment, fmt, swizzle = struct.unpack_from("<HHHBB", imag, 8)
    data_size = struct.unpack_from("<I", imag, 0x10)[0]
    pica_fmt = _BFLIM_FORMATS.get(fmt)
    if pica_fmt is None:
        raise BflimError(f"unsupported BFLIM format {fmt}")
    payload = data[:data_size]

    # Swizzle 4/8 store the image rotated 90°; decode transposed then rotate.
    if swizzle in (4, 8):
        # Texture is stored with swapped dimensions.
        stored_w = _pad8(height)
        stored_h = _pad8(width)
        rgba = decode_pica_texture(payload, stored_w, stored_h, pica_fmt)
        rgba = _rotate_ccw(rgba, stored_w, stored_h)
        rgba = _crop(rgba, stored_h, stored_w, width, height)
    else:
        stored_w = _pad8(width)
        stored_h = _pad8(height)
        rgba = decode_pica_texture(payload, stored_w, stored_h, pica_fmt)
        rgba = _crop(rgba, stored_w, stored_h, width, height)
    return Bflim(width=width, height=height, format=fmt, swizzle=swizzle, rgba=rgba)


def _pad8(v: int) -> int:
    return (v + 7) & ~7


def _crop(rgba: bytes, src_w: int, src_h: int, dst_w: int, dst_h: int) -> bytes:
    if src_w == dst_w and src_h == dst_h:
        return rgba
    out = bytearray(dst_w * dst_h * 4)
    for y in range(min(dst_h, src_h)):
        row = rgba[y * src_w * 4 : (y * src_w + min(dst_w, src_w)) * 4]
        out[y * dst_w * 4 : y * dst_w * 4 + len(row)] = row
    return bytes(out)


def _rotate_ccw(rgba: bytes, w: int, h: int) -> bytes:
    """Rotate 90° counter-clockwise: (w,h) -> (h,w)."""
    out = bytearray(w * h * 4)
    for y in range(h):
        for x in range(w):
            src = (y * w + x) * 4
            dst = ((w - 1 - x) * h + y) * 4
            out[dst : dst + 4] = rgba[src : src + 4]
    return bytes(out)
