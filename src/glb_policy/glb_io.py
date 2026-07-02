"""Read and write binary GLB files."""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

_GLTF_MAGIC = 0x46546C67
_JSON_CHUNK = 0x4E4F534A
_BIN_CHUNK = 0x004E4942


@dataclass
class GlbData:
    json: dict
    bin_chunk: bytes

    def write(self, path: Path) -> None:
        json_bytes = json.dumps(self.json, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        json_pad = (4 - (len(json_bytes) % 4)) % 4
        json_bytes += b" " * json_pad

        bin_chunk = self.bin_chunk
        bin_pad = (4 - (len(bin_chunk) % 4)) % 4
        bin_chunk_padded = bin_chunk + b"\x00" * bin_pad

        total_length = 12 + 8 + len(json_bytes)
        if bin_chunk_padded:
            total_length += 8 + len(bin_chunk_padded)

        out = bytearray()
        out.extend(struct.pack("<III", _GLTF_MAGIC, 2, total_length))
        out.extend(struct.pack("<II", len(json_bytes), _JSON_CHUNK))
        out.extend(json_bytes)
        if bin_chunk_padded:
            out.extend(struct.pack("<II", len(bin_chunk_padded), _BIN_CHUNK))
            out.extend(bin_chunk_padded)
        path.write_bytes(out)


def read_glb(path: Path) -> GlbData:
    data = path.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB too small")
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != _GLTF_MAGIC:
        raise ValueError("Not a GLB file")

    offset = 12
    gltf: dict | None = None
    bin_chunk = b""
    while offset + 8 <= len(data):
        chunk_length, chunk_type = struct.unpack_from("<II", data, offset)
        chunk_start = offset + 8
        chunk_end = chunk_start + chunk_length
        chunk = data[chunk_start:chunk_end]
        if chunk_type == _JSON_CHUNK:
            gltf = json.loads(chunk.decode("utf-8"))
        elif chunk_type == _BIN_CHUNK:
            bin_chunk = bytes(chunk)
        offset = chunk_end
        if offset % 4:
            offset += 4 - (offset % 4)

    if gltf is None:
        raise ValueError("GLB JSON chunk missing")
    return GlbData(json=gltf, bin_chunk=bin_chunk)


def read_glb_json(path: Path) -> dict:
    return read_glb(path).json
