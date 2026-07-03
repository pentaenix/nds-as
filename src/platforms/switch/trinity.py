"""Trinity TRPFS / TRPFD / TRPAK virtual filesystem."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import BinaryIO, Callable

from . import flatbuf as fb
from .fnv import fnv1a64
from .oodle import OodleError, maybe_decompress

ONEPACK_MAGIC = b"ONEPACK\x00"


class TrinityError(RuntimeError):
    pass


@dataclass(slots=True)
class TrpakEntry:
    hash: int
    compression_type: int
    decompressed_size: int
    compressed: bytes


@dataclass(slots=True)
class TrpakFile:
    path: str
    trpak_path: str
    hash: int
    entry: TrpakEntry


@dataclass(slots=True)
class TrpfdIndex:
    file_hashes: list[int]
    trpak_paths: list[str]
    trpak_index_by_hash: dict[int, int]
    file_trpak_index: list[int]

    @classmethod
    def parse(cls, data: bytes) -> "TrpfdIndex":
        root = fb.root_offset(data)
        fields = fb.table_fields(data, root).offsets
        file_hashes = fb.read_u64_vector(data, fields[0])
        trpak_paths = fb.read_string_vector(data, fields[1])
        map_tables = fb.read_table_vector(data, fields[2])
        file_trpak_index = [
            fb.u32(data, mt.offsets[0]) if mt.offsets[0] is not None else 0 for mt in map_tables
        ]
        if len(file_trpak_index) != len(file_hashes):
            raise TrinityError("TRPFD map length mismatch")
        trpak_index_by_hash = {fnv1a64(p): i for i, p in enumerate(trpak_paths)}
        return cls(
            file_hashes=file_hashes,
            trpak_paths=trpak_paths,
            trpak_index_by_hash=trpak_index_by_hash,
            file_trpak_index=file_trpak_index,
        )


@dataclass(slots=True)
class TrpfsIndex:
    hashes: list[int]
    offsets: list[int]
    data_base: int

    @classmethod
    def parse_header(cls, header: bytes, index_region: bytes) -> "TrpfsIndex":
        if not header.startswith(ONEPACK_MAGIC):
            raise TrinityError("Not a ONEPACK TRPFS")
        init_offset = struct.unpack_from("<Q", header, 8)[0]
        root = fb.root_offset(index_region)
        fields = fb.table_fields(index_region, root).offsets
        hashes = fb.read_u64_vector(index_region, fields[0])
        offsets = fb.read_u64_vector(index_region, fields[1])
        return cls(hashes=hashes, offsets=offsets, data_base=init_offset)

    def slice_range(self, index: int) -> tuple[int, int]:
        start = self.offsets[index]
        end = self.offsets[index + 1] if index + 1 < len(self.offsets) else self.data_base
        return start, end - start


def parse_trpak(data: bytes) -> dict[int, TrpakEntry]:
    root = fb.root_offset(data)
    fields = fb.table_fields(data, root).offsets
    hashes = fb.read_u64_vector(data, fields[0])
    file_tables = fb.read_table_vector(data, fields[1])
    out: dict[int, TrpakEntry] = {}
    for h, ft in zip(hashes, file_tables, strict=False):
        comp = fb.u8(data, ft.offsets[1]) if ft.offsets[1] is not None else 255
        decomp = fb.u64(data, ft.offsets[3]) if ft.offsets[3] is not None else 0
        dvec = fb.vector_offset(data, ft.offsets[4]) if ft.offsets[4] is not None else 0
        clen = fb.vector_len(data, dvec) if dvec else 0
        payload = data[fb.vector_data(data, dvec) : fb.vector_data(data, dvec) + clen] if dvec else b""
        out[h] = TrpakEntry(
            hash=h,
            compression_type=comp,
            decompressed_size=decomp,
            compressed=payload,
        )
    return out


class TrinityArchive:
    """Read-only view over ``data.trpfd`` + ``data.trpfs`` inside a ROM."""

    def __init__(
        self,
        trpfd: bytes,
        trpfs_index: TrpfsIndex,
        *,
        read_trpfs_range: Callable[[int, int], bytes],
    ):
        self._trpfd = TrpfdIndex.parse(trpfd)
        self._trpfs_index = trpfs_index
        self._read_range = read_trpfs_range
        self._trpak_cache: dict[int, dict[int, TrpakEntry]] = {}
        self._trpfs_hash_set = set(trpfs_index.hashes)
        self._file_hash_to_trpak: dict[int, str] = {}
        for file_hash, trpak_index in zip(
            self._trpfd.file_hashes,
            self._trpfd.file_trpak_index,
            strict=False,
        ):
            if trpak_index < len(self._trpfd.trpak_paths):
                self._file_hash_to_trpak[file_hash] = self._trpfd.trpak_paths[trpak_index]

    def trpak_in_trpfs(self, trpak_path: str) -> bool:
        return fnv1a64(trpak_path) in self._trpfs_hash_set

    def resolve_trpak_path(
        self,
        inner_path: str,
        trpak_path: str | None = None,
        *,
        file_hash: int | None = None,
    ) -> str:
        """Return a TRPAK path that exists in this ROM's TRPFS."""
        key = file_hash if file_hash is not None else fnv1a64(inner_path)
        candidates: list[str] = []
        if trpak_path:
            candidates.append(trpak_path.replace("\\", "/"))
        canon = self._file_hash_to_trpak.get(key)
        if canon and canon not in candidates:
            candidates.append(canon)
        for candidate in candidates:
            if self.trpak_in_trpfs(candidate):
                return candidate
        label = trpak_path or canon or inner_path
        raise TrinityError(f"TRPAK not in TRPFS: {label}")

    @property
    def trpak_paths(self) -> list[str]:
        return self._trpfd.trpak_paths

    def _trpak_bytes(self, trpak_path: str) -> bytes:
        if not self.trpak_in_trpfs(trpak_path):
            raise TrinityError(f"TRPAK not in TRPFS: {trpak_path}")
        key = fnv1a64(trpak_path)
        idx = self._trpfs_index.hashes.index(key)
        off, size = self._trpfs_index.slice_range(idx)
        return self._read_range(off, size)

    def _trpak_entries(self, trpak_path: str) -> dict[int, TrpakEntry]:
        key = fnv1a64(trpak_path)
        cached = self._trpak_cache.get(key)
        if cached is not None:
            return cached
        entries = parse_trpak(self._trpak_bytes(trpak_path))
        self._trpak_cache[key] = entries
        return entries

    def read_file(self, trpak_path: str, file_hash: int, *, decompress: bool = True) -> bytes:
        entries = self._trpak_entries(trpak_path)
        entry = entries.get(file_hash)
        if entry is None:
            raise TrinityError(f"Hash {file_hash:#x} not in {trpak_path}")
        if not decompress:
            return entry.compressed
        try:
            return maybe_decompress(entry.compression_type, entry.compressed, entry.decompressed_size)
        except OodleError:
            raise

    def read_by_path(self, trpak_path: str, inner_path: str, *, decompress: bool = True) -> bytes:
        return self.read_file(trpak_path, fnv1a64(inner_path), decompress=decompress)

    def list_trpak(self, trpak_path: str) -> list[TrpakEntry]:
        return list(self._trpak_entries(trpak_path).values())
