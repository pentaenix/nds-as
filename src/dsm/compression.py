from __future__ import annotations


class LZ10Error(ValueError):
    pass


def looks_like_lz10(data: bytes) -> bool:
    if len(data) < 4 or data[0] != 0x10:
        return False
    size = data[1] | (data[2] << 8) | (data[3] << 16)
    # Conservative cap: false-positive LZ headers are common in binary ROM data.
    # Pokémon/DS asset LZ10 payloads DSM needs for browsing are normally much smaller;
    # larger streams can be handled later by explicit export tooling.
    return 0 < size <= 16 * 1024 * 1024


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
