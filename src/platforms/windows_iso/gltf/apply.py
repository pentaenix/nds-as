"""Apply Windows ISO metadata to a glTF 2.0 binary."""
from __future__ import annotations

import json
from pathlib import Path
import struct


_GLB_HEADER = struct.Struct("<4sII")
_GLB_CHUNK = struct.Struct("<I4s")


def apply_platform_glb_policy(glb_path: Path) -> None:
    path = Path(glb_path)
    payload = path.read_bytes()
    if len(payload) < 20:
        raise ValueError("GLB is truncated")
    magic, version, declared_size = _GLB_HEADER.unpack_from(payload)
    if magic != b"glTF" or version != 2 or declared_size != len(payload):
        raise ValueError("invalid GLB header")
    offset = _GLB_HEADER.size
    chunks: list[tuple[bytes, bytes]] = []
    while offset < len(payload):
        if offset + _GLB_CHUNK.size > len(payload):
            raise ValueError("truncated GLB chunk header")
        size, kind = _GLB_CHUNK.unpack_from(payload, offset)
        offset += _GLB_CHUNK.size
        end = offset + size
        if end > len(payload):
            raise ValueError("truncated GLB chunk")
        chunks.append((kind, payload[offset:end]))
        offset = end
    if not chunks or chunks[0][0] != b"JSON":
        raise ValueError("GLB does not begin with JSON")

    document = json.loads(chunks[0][1].rstrip(b" \t\r\n\0"))
    document.setdefault("asset", {}).setdefault("extras", {})["rae"] = {
        "platform": "windows_iso",
        "schemaVersion": 1,
    }
    for material in document.get("materials") or []:
        alpha_mode = material.get("alphaMode", "OPAQUE")
        render_class = (
            "alpha_blend" if alpha_mode == "BLEND"
            else "mask" if alpha_mode == "MASK"
            else "opaque"
        )
        material.setdefault("extras", {})["rae"] = {
            "platform": "windows_iso",
            "schemaVersion": 1,
            "renderClass": render_class,
        }
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    chunks[0] = (b"JSON", encoded)
    body = b"".join(_GLB_CHUNK.pack(len(data), kind) + data for kind, data in chunks)
    path.write_bytes(_GLB_HEADER.pack(b"glTF", 2, _GLB_HEADER.size + len(body)) + body)
