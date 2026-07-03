"""PokeDocs GFPAK hash → path cache for Scarlet/Violet Trinity archives."""
from __future__ import annotations

import urllib.request
from pathlib import Path

from .fnv import fnv1a64

_POKE_DOCS_URL = (
    "https://raw.githubusercontent.com/pkZukan/PokeDocs/main/"
    "SV/Hashlists/FileSystem/hashes_inside_trpak.txt"
)


class HashCache:
    """Maps TRPAK inner file hashes to logical paths."""

    def __init__(self) -> None:
        self._by_hash: dict[int, tuple[str, str]] = {}
        self._by_trpak: dict[str, list[tuple[int, str]]] = {}

    @classmethod
    def load(cls, path: Path) -> "HashCache":
        cache = cls()
        cache._parse(path.read_text(encoding="utf-8", errors="replace"))
        return cache

    @classmethod
    def ensure(cls, cache_path: Path, progress=None) -> "HashCache":
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not cache_path.is_file():
            if progress:
                progress("Switch: downloading PokeDocs hash list…")
            urllib.request.urlretrieve(_POKE_DOCS_URL, cache_path)
        return cls.load(cache_path)

    def _parse(self, text: str) -> None:
        trpak = ""
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("|") or line.startswith("#"):
                continue
            if line.startswith("@"):
                trpak = line[1:].replace("\\", "/")
                if not trpak.startswith("arc/"):
                    trpak = f"arc/{trpak}"
                self._by_trpak.setdefault(trpak, [])
                continue
            if not line.startswith("0x"):
                continue
            hexpart, _, path = line.partition(" ")
            path = path.strip().replace("\\", "/")
            if not path:
                continue
            h = int(hexpart, 16)
            self._by_hash[h] = (trpak, path)
            if trpak:
                self._by_trpak.setdefault(trpak, []).append((h, path))

    def resolve(self, file_hash: int) -> tuple[str, str] | None:
        return self._by_hash.get(file_hash)

    def files_in_trpak(self, trpak_path: str) -> list[tuple[int, str]]:
        return self._by_trpak.get(trpak_path, [])

    def paths_matching(self, *, suffix: str = "", contains: str = "") -> list[tuple[str, str, int]]:
        out: list[tuple[str, str, int]] = []
        suf = suffix.lower()
        needle = contains.lower()
        for h, (trpak, path) in self._by_hash.items():
            lower = path.lower()
            if suf and not lower.endswith(suf):
                continue
            if needle and needle not in lower:
                continue
            out.append((trpak, path, h))
        return out

    def path_hash(self, inner_path: str) -> int:
        return fnv1a64(inner_path)
