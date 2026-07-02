from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from ...core.util import read_u16le, read_u32le, safe_decode


@dataclass(slots=True)
class RomFile:
    file_id: int
    path: str
    start: int
    end: int
    data: bytes

    @property
    def size(self) -> int:
        return max(0, self.end - self.start)


@dataclass(slots=True)
class RomInfo:
    title: str
    game_code: str
    maker_code: str
    fnt_offset: int
    fnt_size: int
    fat_offset: int
    fat_size: int
    file_count: int


class NDSRomError(ValueError):
    pass


class NDSRom:
    """Minimal Nintendo DS ROM filesystem parser.

    It reads the header's File Name Table (FNT) and File Allocation Table (FAT).
    If names are missing or the FNT cannot be parsed, it falls back to numbered files.
    """

    def __init__(self, data: bytes):
        if len(data) < 0x200:
            raise NDSRomError("File is too small to be a Nintendo DS ROM")
        self.data = data
        self.info = self._read_info()
        self._fat_entries = self._read_fat()

    @classmethod
    def from_path(cls, path: str) -> "NDSRom":
        with open(path, "rb") as f:
            return cls(f.read())

    def _read_info(self) -> RomInfo:
        title = safe_decode(self.data[0x000:0x00C]).rstrip("\0 ")
        game_code = safe_decode(self.data[0x00C:0x010]).rstrip("\0 ")
        maker_code = safe_decode(self.data[0x010:0x012]).rstrip("\0 ")
        fnt_offset = read_u32le(self.data, 0x040)
        fnt_size = read_u32le(self.data, 0x044)
        fat_offset = read_u32le(self.data, 0x048)
        fat_size = read_u32le(self.data, 0x04C)
        file_count = fat_size // 8

        if fat_offset <= 0 or fat_offset + fat_size > len(self.data):
            raise NDSRomError("Invalid FAT offset/size in ROM header")

        return RomInfo(
            title=title,
            game_code=game_code,
            maker_code=maker_code,
            fnt_offset=fnt_offset,
            fnt_size=fnt_size,
            fat_offset=fat_offset,
            fat_size=fat_size,
            file_count=file_count,
        )

    def _read_fat(self) -> list[tuple[int, int]]:
        entries: list[tuple[int, int]] = []
        off = self.info.fat_offset
        for _ in range(self.info.file_count):
            start = read_u32le(self.data, off)
            end = read_u32le(self.data, off + 4)
            off += 8
            if start > end or end > len(self.data):
                # Keep the entry but make it empty so one bad entry does not kill scanning.
                start = end = 0
            entries.append((start, end))
        return entries

    def iter_files(self) -> Iterable[RomFile]:
        paths = self._parse_fnt_paths()
        if not paths:
            paths = {i: f"file_{i:05d}.bin" for i in range(len(self._fat_entries))}

        for file_id, (start, end) in enumerate(self._fat_entries):
            path = paths.get(file_id, f"file_{file_id:05d}.bin")
            yield RomFile(
                file_id=file_id,
                path=path,
                start=start,
                end=end,
                data=self.data[start:end],
            )

    def _parse_fnt_paths(self) -> dict[int, str]:
        info = self.info
        if info.fnt_offset <= 0 or info.fnt_size <= 0:
            return {}
        if info.fnt_offset + info.fnt_size > len(self.data):
            return {}

        base = info.fnt_offset
        fnt = self.data[base:base + info.fnt_size]
        if len(fnt) < 8:
            return {}

        # Root directory entry: subtable offset, first file id, total dir count.
        try:
            dir_count = read_u16le(fnt, 6)
            if dir_count <= 0 or dir_count > 4096 or dir_count * 8 > len(fnt):
                return {}

            dir_entries: list[tuple[int, int, int]] = []
            for i in range(dir_count):
                entry_off = i * 8
                subtable_off = read_u32le(fnt, entry_off)
                first_file_id = read_u16le(fnt, entry_off + 4)
                parent_id = read_u16le(fnt, entry_off + 6)
                dir_entries.append((subtable_off, first_file_id, parent_id))

            paths: dict[int, str] = {}
            visited_dirs: set[int] = set()

            def parse_dir(dir_id: int, parent_path: PurePosixPath) -> None:
                index = dir_id & 0x0FFF
                if index < 0 or index >= len(dir_entries) or index in visited_dirs:
                    return
                visited_dirs.add(index)

                subtable_off, first_file_id, _ = dir_entries[index]
                if subtable_off >= len(fnt):
                    return

                pos = subtable_off
                current_file_id = first_file_id

                while pos < len(fnt):
                    control = fnt[pos]
                    pos += 1
                    if control == 0:
                        break

                    is_dir = bool(control & 0x80)
                    name_len = control & 0x7F
                    if name_len == 0 or pos + name_len > len(fnt):
                        break

                    name = safe_decode(fnt[pos:pos + name_len])
                    pos += name_len

                    if is_dir:
                        if pos + 2 > len(fnt):
                            break
                        sub_dir_id = read_u16le(fnt, pos)
                        pos += 2
                        parse_dir(sub_dir_id, parent_path / name)
                    else:
                        paths[current_file_id] = str(parent_path / name)
                        current_file_id += 1

            parse_dir(0xF000, PurePosixPath(""))
            return paths
        except Exception:
            return {}
