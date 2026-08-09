"""PICA200 helpers: GPU command stream reader + texture decoding.

Texture decode covers the swizzled 8x8 Morton tile layout and all formats
used by Pokémon X/Y..USUM GFTexture / BFLIM assets (including ETC1/ETC1A4).
"""
from __future__ import annotations

import struct

# -- GPU registers used by GFMesh command lists ------------------------------
GPUREG_ATTRIBBUFFERS_FORMAT_LOW = 0x0201
GPUREG_ATTRIBBUFFERS_FORMAT_HIGH = 0x0202
GPUREG_ATTRIBBUFFER0_CONFIG1 = 0x0204
GPUREG_ATTRIBBUFFER0_CONFIG2 = 0x0205
GPUREG_INDEXBUFFER_CONFIG = 0x0227
GPUREG_NUMVERTICES = 0x0228
GPUREG_FIXEDATTRIB_INDEX = 0x0232
GPUREG_FIXEDATTRIB_DATA0 = 0x0233
GPUREG_FIXEDATTRIB_DATA1 = 0x0234
GPUREG_FIXEDATTRIB_DATA2 = 0x0235
GPUREG_VSH_NUM_ATTR = 0x0242
GPUREG_PRIMITIVE_CONFIG = 0x025E
GPUREG_VSH_ATTRIBUTES_PERMUTATION_LOW = 0x02BB
GPUREG_VSH_ATTRIBUTES_PERMUTATION_HIGH = 0x02BC


def read_pica_commands(words: list[int]) -> list[tuple[int, int]]:
    """Expand a PICA command buffer into ``(register, parameter)`` pairs."""
    out: list[tuple[int, int]] = []
    idx = 0
    count = len(words)
    while idx + 1 < count:
        param = words[idx]
        header = words[idx + 1]
        idx += 2
        register = header & 0xFFFF
        extra = (header >> 20) & 0x7FF
        consecutive = header >> 31
        out.append((register, param))
        for n in range(extra):
            if idx >= count:
                break
            reg = register + n + 1 if consecutive else register
            out.append((reg, words[idx]))
            idx += 1
        # PICA commands are padded to 8-byte blocks. The next command begins
        # on an even word index regardless of consecutive/non-consecutive mode.
        # (SPICA's PICACommandReader uses the same index-parity rule.)
        if idx & 1:
            idx += 1
    return out


# -- Swizzle -----------------------------------------------------------------

_TILE_ORDER = [
    0, 1, 8, 9, 2, 3, 10, 11,
    16, 17, 24, 25, 18, 19, 26, 27,
    4, 5, 12, 13, 6, 7, 14, 15,
    20, 21, 28, 29, 22, 23, 30, 31,
    32, 33, 40, 41, 34, 35, 42, 43,
    48, 49, 56, 57, 50, 51, 58, 59,
    36, 37, 44, 45, 38, 39, 46, 47,
    52, 53, 60, 61, 54, 55, 62, 63,
]


def _iter_tiled(width: int, height: int):
    """Yield (linear_index, x, y) for the PICA 8x8 Z-order layout."""
    i = 0
    for ty in range(0, height, 8):
        for tx in range(0, width, 8):
            for px in _TILE_ORDER:
                x = tx + (px & 7)
                y = ty + (px >> 3)
                yield i, x, y
                i += 1


# -- ETC1 --------------------------------------------------------------------

_ETC1_LUT = [
    (2, 8), (5, 17), (9, 29), (13, 42),
    (18, 60), (24, 80), (33, 106), (47, 183),
]


def _clamp(v: int) -> int:
    return 0 if v < 0 else 255 if v > 255 else v


def _decode_etc1_block(block: int) -> list[tuple[int, int, int]]:
    """Decode one 4x4 ETC1 colour block (64-bit int) to row-major RGB."""
    diff = (block >> 33) & 1
    flip = (block >> 32) & 1
    table1 = (block >> 37) & 7
    table2 = (block >> 34) & 7
    if diff:
        r1 = (block >> 59) & 0x1F
        g1 = (block >> 51) & 0x1F
        b1 = (block >> 43) & 0x1F
        dr = (block >> 56) & 7
        dg = (block >> 48) & 7
        db = (block >> 40) & 7
        if dr >= 4:
            dr -= 8
        if dg >= 4:
            dg -= 8
        if db >= 4:
            db -= 8
        r2, g2, b2 = r1 + dr, g1 + dg, b1 + db
        c1 = (r1 << 3 | r1 >> 2, g1 << 3 | g1 >> 2, b1 << 3 | b1 >> 2)
        c2 = (r2 << 3 | r2 >> 2, g2 << 3 | g2 >> 2, b2 << 3 | b2 >> 2)
    else:
        r1 = (block >> 60) & 0xF
        g1 = (block >> 52) & 0xF
        b1 = (block >> 44) & 0xF
        r2 = (block >> 56) & 0xF
        g2 = (block >> 48) & 0xF
        b2 = (block >> 40) & 0xF
        c1 = (r1 * 17, g1 * 17, b1 * 17)
        c2 = (r2 * 17, g2 * 17, b2 * 17)
    out: list[tuple[int, int, int]] = [(0, 0, 0)] * 16
    for px in range(16):
        x, y = px >> 2, px & 3  # ETC1 stores column-major
        msb = (block >> (px + 16)) & 1
        lsb = (block >> px) & 1
        # Sub-block selection.
        second = x >= 2 if not flip else y >= 2
        base = c2 if second else c1
        table = table2 if second else table1
        small, big = _ETC1_LUT[table]
        if msb:
            mod = -big if lsb else -small
        else:
            mod = big if lsb else small
        out[y * 4 + x] = (_clamp(base[0] + mod), _clamp(base[1] + mod), _clamp(base[2] + mod))
    return out


def _decode_etc1(data: bytes, width: int, height: int, alpha: bool) -> bytearray:
    out = bytearray(width * height * 4)
    pos = 0
    for ty in range(0, height, 8):
        for tx in range(0, width, 8):
            # Each 8x8 tile = 4x (4x4 blocks) in order TL, TR? -> actually
            # 2x2 blocks ordered: (0,0), (4,0)? PICA order: x-then-y minor.
            for by in (0, 4):
                for bx in (0, 4):
                    if alpha:
                        alpha_block = int.from_bytes(data[pos : pos + 8], "little")
                        pos += 8
                    else:
                        alpha_block = None
                    color_block = int.from_bytes(data[pos : pos + 8], "little")
                    pos += 8
                    pixels = _decode_etc1_block(color_block)
                    for px in range(16):
                        x = tx + bx + (px & 3)
                        y = ty + by + (px >> 2)
                        if x >= width or y >= height:
                            continue
                        r, g, b = pixels[px]
                        if alpha_block is None:
                            a = 255
                        else:
                            # 4-bit alpha, column-major nibbles.
                            col, row = (px & 3), (px >> 2)
                            shift = (col * 4 + row) * 4
                            a = ((alpha_block >> shift) & 0xF) * 17
                        off = (y * width + x) * 4
                        out[off : off + 4] = bytes((r, g, b, a))
    return out


# -- Full texture decode -----------------------------------------------------

PICA_FORMATS = {
    "RGBA8": 0,
    "RGB8": 1,
    "RGBA5551": 2,
    "RGB565": 3,
    "RGBA4": 4,
    "LA8": 5,
    "HILO8": 6,
    "L8": 7,
    "A8": 8,
    "LA4": 9,
    "L4": 10,
    "A4": 11,
    "ETC1": 12,
    "ETC1A4": 13,
}

BYTES_PER_PIXEL = {0: 4, 1: 3, 2: 2, 3: 2, 4: 2, 5: 2, 6: 2, 7: 1, 8: 1, 9: 1}


def decode_pica_texture(data: bytes, width: int, height: int, fmt: int) -> bytes:
    """Decode a swizzled PICA texture to RGBA8888 bytes (row 0 = top)."""
    if fmt in (12, 13):
        rgba = _decode_etc1(data, width, height, alpha=(fmt == 13))
        return bytes(rgba)

    out = bytearray(width * height * 4)

    def put(x: int, y: int, r: int, g: int, b: int, a: int) -> None:
        off = (y * width + x) * 4
        out[off] = r
        out[off + 1] = g
        out[off + 2] = b
        out[off + 3] = a

    if fmt in (10, 11):  # L4 / A4: nibble per pixel
        for i, x, y in _iter_tiled(width, height):
            nib = (data[i >> 1] >> ((i & 1) * 4)) & 0xF
            v = nib * 17
            if fmt == 10:
                put(x, y, v, v, v, 255)
            else:
                put(x, y, 255, 255, 255, v)
        return bytes(out)

    bpp = BYTES_PER_PIXEL[fmt]
    for i, x, y in _iter_tiled(width, height):
        o = i * bpp
        if fmt == 0:  # RGBA8 stored ABGR
            a, b, g, r = data[o], data[o + 1], data[o + 2], data[o + 3]
            put(x, y, r, g, b, a)
        elif fmt == 1:  # RGB8 stored BGR
            b, g, r = data[o], data[o + 1], data[o + 2]
            put(x, y, r, g, b, 255)
        elif fmt == 2:  # RGBA5551
            v = data[o] | data[o + 1] << 8
            r = ((v >> 11) & 0x1F) * 255 // 31
            g = ((v >> 6) & 0x1F) * 255 // 31
            b = ((v >> 1) & 0x1F) * 255 // 31
            a = (v & 1) * 255
            put(x, y, r, g, b, a)
        elif fmt == 3:  # RGB565
            v = data[o] | data[o + 1] << 8
            r = ((v >> 11) & 0x1F) * 255 // 31
            g = ((v >> 5) & 0x3F) * 255 // 63
            b = (v & 0x1F) * 255 // 31
            put(x, y, r, g, b, 255)
        elif fmt == 4:  # RGBA4
            v = data[o] | data[o + 1] << 8
            r = ((v >> 12) & 0xF) * 17
            g = ((v >> 8) & 0xF) * 17
            b = ((v >> 4) & 0xF) * 17
            a = (v & 0xF) * 17
            put(x, y, r, g, b, a)
        elif fmt == 5:  # LA8 — luminance then alpha per texel
            lum, a = data[o], data[o + 1]
            put(x, y, lum, lum, lum, a)
        elif fmt == 6:  # HILO8 (normal map RG)
            g, r = data[o], data[o + 1]
            put(x, y, r, g, 255, 255)
        elif fmt == 7:  # L8
            lum = data[o]
            put(x, y, lum, lum, lum, 255)
        elif fmt == 8:  # A8
            put(x, y, 255, 255, 255, data[o])
        elif fmt == 9:  # LA4
            v = data[o]
            put(x, y, (v >> 4) * 17, (v >> 4) * 17, (v >> 4) * 17, (v & 0xF) * 17)
    return bytes(out)


def rgba_to_png(rgba: bytes, width: int, height: int) -> bytes:
    """Encode RGBA8888 rows to PNG without external dependencies."""
    import zlib

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)
        raw.extend(rgba[y * stride : (y + 1) * stride])

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )
