"""Oodle Kraken decompression for Trinity TRPAK payloads.

Nintendo Switch titles store most TRPAK members as Oodle-compressed blobs
(compression_type == 3). RAE never ships Epic's ``oo2core`` library — point
``RAE_SWITCH_OODLE_DLL`` at ``oo2core_8_win64.dll``, ``liboo2core.so``, or
``liboo2core.dylib`` from a legal source (e.g. a game install you own).
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

_COMP_OODLE = 3
_COMP_NONE = 255

_OODLE_SEARCH = (
    "RAE_SWITCH_OODLE_DLL",
    "OODLE_DLL",
    "OODLE_LIBRARY_PATH",
)


class OodleError(RuntimeError):
    pass


_lib: ctypes.CDLL | None = None


def _find_library() -> Path | None:
    for env in _OODLE_SEARCH:
        val = os.environ.get(env)
        if val and Path(val).is_file():
            return Path(val)
    rae_root = Path(__file__).resolve().parents[3]
    for name in (
        "liboo2core.dylib",
        "liboo2coremac64.2.9.12.dylib",
        "liboo2coremac64.dylib",
        "oo2core_9_win64.dll",
        "oo2core_8_win64.dll",
        "oo2core_7_win64.dll",
    ):
        candidate = rae_root / "tools" / name
        if candidate.is_file():
            return candidate
    return None


def _load() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    path = _find_library()
    if path is None:
        raise OodleError(
            "Oodle library not found. Set RAE_SWITCH_OODLE_DLL to oo2core "
            "(oo2core_8_win64.dll / liboo2core.dylib) to decompress models."
        )
    lib = ctypes.CDLL(str(path))
    lib.OodleLZ_Decompress.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    lib.OodleLZ_Decompress.restype = ctypes.c_int64
    _lib = lib
    return lib


def decompress(compressed: bytes, decompressed_size: int) -> bytes:
    lib = _load()
    out = ctypes.create_string_buffer(decompressed_size)
    src = ctypes.create_string_buffer(compressed, len(compressed))
    n = lib.OodleLZ_Decompress(
        ctypes.cast(src, ctypes.c_void_p),
        len(compressed),
        ctypes.cast(out, ctypes.c_void_p),
        decompressed_size,
        0,
        0,
        0,
        None,
        0,
        None,
        None,
        None,
        None,
        3,
    )
    if n <= 0:
        raise OodleError("OodleLZ_Decompress failed")
    return out.raw[:decompressed_size]


def maybe_decompress(compression_type: int, payload: bytes, decompressed_size: int) -> bytes:
    if compression_type in (0, _COMP_NONE):
        return payload
    if compression_type == _COMP_OODLE:
        return decompress(payload, decompressed_size)
    raise OodleError(f"Unsupported TRPAK compression type {compression_type}")
