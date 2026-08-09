from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ...core.util import read_u16le, read_u32le


@dataclass(slots=True)
class NarcFile:
    index: int
    path: str
    data: bytes
    start: int
    end: int

    @property
    def size(self) -> int:
        return len(self.data)


class NarcError(ValueError):
    pass


def looks_like_narc(data: bytes) -> bool:
    return len(data) >= 0x10 and data[:4] == b"NARC"


class NarcArchive:
    """Minimal NARC reader.

    Many DS games store files in NARC containers. This parser focuses on extracting
    payloads reliably. It preserves names when a simple BTNF table is present, but
    numbered files are enough for model hunting.
    """

    def __init__(self, data: bytes, virtual_path: str = "archive.narc"):
        if not looks_like_narc(data):
            raise NarcError("Not a NARC archive")
        self.data = data
        self.virtual_path = virtual_path
        self._chunks = self._read_chunks()

    def _read_chunks(self) -> dict[bytes, bytes]:
        if len(self.data) < 0x10:
            raise NarcError("NARC is too small")

        header_size = read_u16le(self.data, 0x0C)
        chunk_count = read_u16le(self.data, 0x0E)
        if header_size < 0x10 or header_size >= len(self.data):
            header_size = 0x10

        chunks: dict[bytes, bytes] = {}
        pos = header_size
        for _ in range(max(0, min(chunk_count, 16))):
            if pos + 8 > len(self.data):
                break
            magic = self.data[pos:pos + 4]
            size = read_u32le(self.data, pos + 4)
            if size < 8 or pos + size > len(self.data):
                break
            chunks[magic] = self.data[pos + 8:pos + size]
            pos += size
            if pos & 3:
                pos = (pos + 3) & ~3
        return chunks

    def iter_files(self) -> Iterable[NarcFile]:
        btaf = self._chunks.get(b"BTAF")
        gmif = self._chunks.get(b"GMIF")
        if not btaf or gmif is None:
            return []

        if len(btaf) < 4:
            return []
        count = read_u16le(btaf, 0)
        entries_off = 4
        if count <= 0 or entries_off + count * 8 > len(btaf):
            return []

        names = self._parse_btnf_names(count)
        files: list[NarcFile] = []
        for index in range(count):
            off = entries_off + index * 8
            start = read_u32le(btaf, off)
            end = read_u32le(btaf, off + 4)
            if start > end or end > len(gmif):
                payload = b""
                start = end = 0
            else:
                payload = gmif[start:end]
            inner_name = names.get(index, f"file_{index:04d}.bin")
            files.append(NarcFile(index=index, path=inner_name, data=payload, start=start, end=end))
        return files

    def _parse_btnf_names(self, count: int) -> dict[int, str]:
        btnf = self._chunks.get(b"BTNF")
        if not btnf or len(btnf) < 8:
            return {}

        names: dict[int, str] = {}

        # NARC BTNF uses a Nitro FNT-like structure. The root table often starts
        # with one directory entry: subtable offset, first file id, directory count.
        try:
            subtable_off = read_u32le(btnf, 0)
            first_file_id = read_u16le(btnf, 4)
            if subtable_off >= len(btnf):
                return {}

            pos = subtable_off
            current_id = first_file_id
            while pos < len(btnf) and current_id < count:
                control = btnf[pos]
                pos += 1
                if control == 0:
                    break
                is_dir = bool(control & 0x80)
                name_len = control & 0x7F
                if name_len == 0 or pos + name_len > len(btnf):
                    break
                raw_name = btnf[pos:pos + name_len]
                pos += name_len
                if is_dir:
                    pos += 2
                    continue
                names[current_id] = raw_name.decode("shift_jis", errors="replace")
                current_id += 1
        except Exception:
            return {}
        return names
