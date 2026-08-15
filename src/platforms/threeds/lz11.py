"""LZ11 decompression for 3DS GARC sub-files.

Maintained inside the 3DS module because platform isolation prevents importing
the Nintendo DS implementation.
"""
from __future__ import annotations


class Lz11Error(ValueError):
    pass


def looks_like_lz11(data: bytes) -> bool:
    return len(data) >= 4 and data[0] == 0x11


def decompress_lz11(data: bytes) -> bytes:
    if not looks_like_lz11(data):
        raise Lz11Error("Not an LZ11 stream")
    out_size = data[1] | data[2] << 8 | data[3] << 16
    pos = 4
    if out_size == 0:
        if len(data) < 8:
            raise Lz11Error("Extended LZ11 header truncated")
        out_size = int.from_bytes(data[4:8], "little")
        pos = 8
    out = bytearray()
    total = len(data)
    try:
        while len(out) < out_size:
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if len(out) >= out_size:
                    break
                if not (flags >> (7 - bit)) & 1:
                    out.append(data[pos])
                    pos += 1
                    continue
                b1 = data[pos]
                indicator = b1 >> 4
                if indicator == 0:
                    b2, b3 = data[pos + 1], data[pos + 2]
                    pos += 3
                    length = ((b1 & 0xF) << 4 | b2 >> 4) + 0x11
                    disp = ((b2 & 0xF) << 8 | b3) + 1
                elif indicator == 1:
                    b2, b3, b4 = data[pos + 1], data[pos + 2], data[pos + 3]
                    pos += 4
                    length = ((b1 & 0xF) << 12 | b2 << 4 | b3 >> 4) + 0x111
                    disp = ((b3 & 0xF) << 8 | b4) + 1
                else:
                    b2 = data[pos + 1]
                    pos += 2
                    length = (b1 >> 4) + 1
                    disp = ((b1 & 0xF) << 8 | b2) + 1
                if disp > len(out):
                    raise Lz11Error("Invalid LZ11 back-reference")
                for _ in range(length):
                    out.append(out[-disp])
                if pos > total:
                    raise Lz11Error("Unexpected end of LZ11 stream")
    except IndexError as exc:
        raise Lz11Error("Truncated LZ11 stream") from exc
    return bytes(out)


def maybe_decompress(data: bytes) -> bytes:
    """Return LZ11-decompressed payload when *data* is a stream, else data."""
    if looks_like_lz11(data):
        try:
            return decompress_lz11(data)
        except Lz11Error:
            return data
    return data
