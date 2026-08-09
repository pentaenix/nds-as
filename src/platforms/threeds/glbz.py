"""Lossless zstd container for lean 3DS GLB exports."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import struct
import tempfile

MAGIC = b"PRGLBZ01"
HEADER = struct.Struct("<8sQQ32s")
COMPRESSION_LEVEL = 19


def _compress(source: bytes) -> bytes:
    try:
        from compression import zstd

        return zstd.compress(source, level=COMPRESSION_LEVEL)
    except ImportError:
        try:
            import zstandard
        except ImportError as exc:
            raise RuntimeError(
                "GLBZ export requires Python 3.14 compression.zstd or the zstandard package"
            ) from exc
        return zstandard.ZstdCompressor(level=COMPRESSION_LEVEL).compress(source)


def _decompress(payload: bytes) -> bytes:
    try:
        from compression import zstd

        return zstd.decompress(payload)
    except ImportError:
        try:
            import zstandard
        except ImportError as exc:
            raise RuntimeError(
                "GLBZ import requires Python 3.14 compression.zstd or the zstandard package"
            ) from exc
        return zstandard.ZstdDecompressor().decompress(payload)


def encode_glbz(source: bytes) -> bytes:
    if len(source) < 8 or source[:4] != b"glTF" or struct.unpack_from("<I", source, 4)[0] != 2:
        raise ValueError("GLBZ source is not a GLB 2.0 container")
    compressed = _compress(source)
    digest = hashlib.sha256(source).digest()
    return HEADER.pack(MAGIC, len(source), len(compressed), digest) + compressed


def decode_glbz(container: bytes) -> bytes:
    if len(container) < HEADER.size:
        raise ValueError("GLBZ container is smaller than its header")
    magic, original_size, compressed_size, expected_digest = HEADER.unpack_from(container)
    if magic != MAGIC:
        raise ValueError("GLBZ container has an unsupported magic/version")
    payload = container[HEADER.size:]
    if len(payload) != compressed_size:
        raise ValueError("GLBZ compressed payload size does not match its header")
    restored = _decompress(payload)
    if len(restored) != original_size:
        raise ValueError("GLBZ decompressed size does not match its header")
    if hashlib.sha256(restored).digest() != expected_digest:
        raise ValueError("GLBZ decompressed SHA-256 does not match its header")
    return restored


def write_glbz(glb_path: str | Path, out_path: str | Path) -> Path:
    source_path = Path(glb_path)
    destination = Path(out_path)
    source = source_path.read_bytes()
    container = encode_glbz(source)
    if decode_glbz(container) != source:
        raise ValueError(f"GLBZ round trip failed for {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(container)
        handle.flush()
        os.fsync(handle.fileno())
    temporary.chmod(0o644)
    temporary.replace(destination)
    if decode_glbz(destination.read_bytes()) != source:
        raise ValueError(f"Written GLBZ round trip failed for {destination}")
    return destination
