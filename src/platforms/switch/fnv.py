"""FNV-1a 64-bit path hashing (Game Freak GFPAK / Trinity)."""
from __future__ import annotations

_FNV_OFFSET = 14695981039346656837
_FNV_PRIME = 1099511628211


def fnv1a64(path: str) -> int:
    h = _FNV_OFFSET
    for ch in path:
        h ^= ord(ch)
        h = (h * _FNV_PRIME) % (2**64)
    return h
