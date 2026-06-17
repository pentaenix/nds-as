from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MAX_LZ_DECOMPRESSED_SIZE = 16 * 1024 * 1024


class LZError(ValueError):
    pass


class LZ10Error(LZError):
    pass


class LZ11Error(LZError):
    pass


@dataclass(frozen=True, slots=True)
class DecompressionAttempt:
    name: str
    decoded: bytes
    expected_size: int


def looks_like_lz10(data: bytes) -> bool:
    if len(data) < 4 or data[0] != 0x10:
        return False
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    # Conservative cap: false-positive LZ headers are common in binary ROM data.
    # Pokémon/DS asset LZ10 payloads DSM needs for browsing are normally much smaller;
    # larger streams can be handled later by explicit export tooling.
    return 0 < size <= MAX_LZ_DECOMPRESSED_SIZE


def looks_like_lz11(data: bytes) -> bool:
    try:
        size, _header_size = _lz11_header(data)
    except LZ11Error:
        return False
    return 0 < size <= MAX_LZ_DECOMPRESSED_SIZE


def lz_expected_size(data: bytes) -> int | None:
    if looks_like_lz10(data):
        return data[1] | (data[2] << 8) | (data[3] << 16)
    try:
        size, _header_size = _lz11_header(data)
    except LZ11Error:
        return None
    return size if 0 < size <= MAX_LZ_DECOMPRESSED_SIZE else None


def decompress_lz10(data: bytes) -> bytes:
    """Decompress Nintendo DS/GBA LZ10 data.

    The stream starts with 0x10 followed by a 24-bit little-endian decompressed size.
    """
    if not looks_like_lz10(data):
        raise LZ10Error("Not an LZ10 stream")

    out_size = data[1] | (data[2] << 8) | (data[3] << 16)
    out = bytearray()
    pos = 4

    while len(out) < out_size:
        if pos >= len(data):
            raise LZ10Error("Unexpected end of LZ10 stream while reading flags")
        flags = data[pos]
        pos += 1

        for bit in range(7, -1, -1):
            if len(out) >= out_size:
                break

            if flags & (1 << bit):
                if pos + 1 >= len(data):
                    raise LZ10Error("Unexpected end of LZ10 stream while reading reference")
                b1 = data[pos]
                b2 = data[pos + 1]
                pos += 2

                length = (b1 >> 4) + 3
                disp = ((b1 & 0x0F) << 8) | b2
                disp += 1

                if disp > len(out):
                    raise LZ10Error("Invalid LZ10 back-reference")

                for _ in range(length):
                    out.append(out[-disp])
                    if len(out) >= out_size:
                        break
            else:
                if pos >= len(data):
                    raise LZ10Error("Unexpected end of LZ10 stream while reading literal")
                out.append(data[pos])
                pos += 1

    return bytes(out)


def decompress_lz11(data: bytes) -> bytes:
    """Decompress Nintendo DS LZ11 data.

    LZ11 uses the same flag-byte structure as LZ10, but its compressed references
    have short, medium, and long encodings. The decompressed size is either a
    24-bit value at bytes 1..3, or a 32-bit value at bytes 4..7 when the 24-bit
    size field is zero.
    """
    out_size, pos = _lz11_header(data)
    if out_size <= 0 or out_size > MAX_LZ_DECOMPRESSED_SIZE:
        raise LZ11Error("Not an LZ11 stream")

    out = bytearray()
    while len(out) < out_size:
        if pos >= len(data):
            raise LZ11Error("Unexpected end of LZ11 stream while reading flags")
        flags = data[pos]
        pos += 1

        for bit in range(7, -1, -1):
            if len(out) >= out_size:
                break
            if not (flags & (1 << bit)):
                if pos >= len(data):
                    raise LZ11Error("Unexpected end of LZ11 stream while reading literal")
                out.append(data[pos])
                pos += 1
                continue

            if pos + 1 >= len(data):
                raise LZ11Error("Unexpected end of LZ11 stream while reading reference")
            b1 = data[pos]
            b2 = data[pos + 1]
            high = b1 >> 4
            if high == 0:
                if pos + 2 >= len(data):
                    raise LZ11Error("Unexpected end of LZ11 medium reference")
                b3 = data[pos + 2]
                length = (((b1 & 0x0F) << 4) | (b2 >> 4)) + 0x11
                disp = ((b2 & 0x0F) << 8) | b3
                pos += 3
            elif high == 1:
                if pos + 3 >= len(data):
                    raise LZ11Error("Unexpected end of LZ11 long reference")
                b3 = data[pos + 2]
                b4 = data[pos + 3]
                length = (((b1 & 0x0F) << 12) | (b2 << 4) | (b3 >> 4)) + 0x111
                disp = ((b3 & 0x0F) << 8) | b4
                pos += 4
            else:
                length = high + 1
                disp = ((b1 & 0x0F) << 8) | b2
                pos += 2

            disp += 1
            if disp > len(out):
                raise LZ11Error("Invalid LZ11 back-reference")
            for _ in range(length):
                out.append(out[-disp])
                if len(out) >= out_size:
                    break

    return bytes(out)


def iter_decompression_attempts(
    data: bytes,
    *,
    enabled: Iterable[str] = ("lz10",),
) -> list[DecompressionAttempt]:
    """Try enabled DS decompression codecs and return successful decodes.

    The caller decides which codecs are worth trying. Fast scans can keep this
    at LZ10 only, while mapping-guided scans can include LZ11 on high-value
    archives without paying that cost across the whole ROM.
    """
    enabled_set = {str(name).casefold() for name in enabled}
    attempts: list[DecompressionAttempt] = []
    if "lz10" in enabled_set and looks_like_lz10(data):
        expected = data[1] | (data[2] << 8) | (data[3] << 16)
        attempts.append(DecompressionAttempt("lz10", decompress_lz10(data), expected))
    if "lz11" in enabled_set and looks_like_lz11(data):
        expected, _header_size = _lz11_header(data)
        attempts.append(DecompressionAttempt("lz11", decompress_lz11(data), expected))
    return attempts


def _lz11_header(data: bytes) -> tuple[int, int]:
    if len(data) < 4 or data[0] != 0x11:
        raise LZ11Error("Not an LZ11 stream")
    size24 = data[1] | (data[2] << 8) | (data[3] << 16)
    if size24:
        return size24, 4
    if len(data) < 8:
        raise LZ11Error("Extended-size LZ11 header is truncated")
    size32 = int.from_bytes(data[4:8], "little", signed=False)
    return size32, 8
