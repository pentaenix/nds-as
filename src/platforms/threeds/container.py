"""Nintendo 3DS container parsing (NCSD / NCCH / RomFS / GARC).

Self-contained inside the 3DS island — no imports from other platforms.

Only *decrypted* dumps are supported: RAE never ships or applies AES keys,
boot9, or seeddb. A ``.cci``/``.3ds`` produced by a legal cartridge dump with
the NoCrypto flag set (or a decrypted ``.cxi``) parses directly; an encrypted
image is detected and reported rather than mis-parsed.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

MEDIA_UNIT = 0x200


class ThreedsCryptoError(RuntimeError):
    """Raised when an image is still encrypted and cannot be parsed keylessly."""


@dataclass(slots=True)
class RomFsFile:
    path: str          # e.g. "/a/0/9/4"
    offset: int        # absolute byte offset into the ROM image
    size: int


@dataclass(slots=True)
class GarcSubFile:
    index: int
    offset: int        # absolute byte offset into the ROM image
    size: int


@dataclass(slots=True)
class NcchInfo:
    partition_index: int
    offset: int
    exefs_offset: int
    exefs_size: int
    romfs_offset: int
    romfs_size: int
    product_code: str
    encrypted: bool


class ThreedsImage:
    """Lazy reader over a decrypted NCSD (.cci/.3ds) or NCCH (.cxi) image."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self._fh = self.path.open("rb")
        self.partitions: list[NcchInfo] = []
        self._parse()

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "ThreedsImage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def read(self, offset: int, size: int) -> bytes:
        self._fh.seek(offset)
        return self._fh.read(size)

    # -- parsing -----------------------------------------------------------
    def _parse(self) -> None:
        head = self.read(0, 0x200)
        magic_ncsd = head[0x100:0x104]
        magic_ncch = head[0x100:0x104]
        if magic_ncsd == b"NCSD":
            self._parse_ncsd()
        elif magic_ncch == b"NCCH":
            self.partitions = [self._parse_ncch(0, 0)]
        else:
            # Some .3ds dumps have a header offset; try NCCH at 0.
            raise ThreedsCryptoError(
                f"{self.path.name}: not a recognizable NCSD/NCCH image "
                "(need a decrypted .cci/.3ds/.cxi)."
            )

    def _parse_ncsd(self) -> None:
        table = self.read(0x120, 8 * 8)
        for i in range(8):
            off_units, len_units = struct.unpack_from("<II", table, i * 8)
            if len_units == 0:
                continue
            part_offset = off_units * MEDIA_UNIT
            magic = self.read(part_offset + 0x100, 4)
            if magic != b"NCCH":
                continue
            self.partitions.append(self._parse_ncch(i, part_offset))

    def _parse_ncch(self, index: int, offset: int) -> NcchInfo:
        h = self.read(offset, 0x200)
        if h[0x100:0x104] != b"NCCH":
            raise ThreedsCryptoError(f"partition {index}: missing NCCH magic")
        product_code = h[0x150:0x160].split(b"\x00", 1)[0].decode("ascii", "replace")
        exefs_off, exefs_size = struct.unpack_from("<II", h, 0x1A0)
        romfs_off, romfs_size = struct.unpack_from("<II", h, 0x1B0)
        flags = h[0x188:0x190]
        # flags[7]: bit0 fixed key, bit2 (0x4) NoCrypto. bit0 of flags[3] also.
        no_crypto = bool(flags[7] & 0x04)
        encrypted = not no_crypto
        return NcchInfo(
            partition_index=index,
            offset=offset,
            exefs_offset=offset + exefs_off * MEDIA_UNIT if exefs_size else 0,
            exefs_size=exefs_size * MEDIA_UNIT,
            romfs_offset=offset + romfs_off * MEDIA_UNIT if romfs_size else 0,
            romfs_size=romfs_size * MEDIA_UNIT,
            product_code=product_code,
            encrypted=encrypted,
        )

    # -- RomFS -------------------------------------------------------------
    def main_partition(self) -> NcchInfo | None:
        for part in self.partitions:
            if part.romfs_size:
                return part
        return self.partitions[0] if self.partitions else None

    def romfs_files(self, part: NcchInfo | None = None) -> list[RomFsFile]:
        part = part or self.main_partition()
        if part is None or not part.romfs_size:
            return []
        if part.encrypted:
            raise ThreedsCryptoError(
                f"{self.path.name}: partition {part.partition_index} is still AES-encrypted. "
                "RAE only reads decrypted dumps (NoCrypto). Re-dump with decryption enabled."
            )
        return _parse_romfs(self, part.romfs_offset)


def _parse_romfs(image: ThreedsImage, romfs_abs: int) -> list[RomFsFile]:
    iv = image.read(romfs_abs, 0x60)
    if iv[:4] != b"IVFC":
        raise ThreedsCryptoError("RomFS IVFC header missing (image likely encrypted).")
    # Level-3 (data) metadata header sits at a 0x1000-aligned offset in USUM/SM.
    lvl3 = romfs_abs + 0x1000
    hdr = struct.unpack("<10I", image.read(lvl3, 0x28))
    dir_meta_off, dir_meta_sz = hdr[3], hdr[4]
    file_meta_off, file_meta_sz = hdr[7], hdr[8]
    file_data_off = hdr[9]
    dir_meta = image.read(lvl3 + dir_meta_off, dir_meta_sz)
    file_meta = image.read(lvl3 + file_meta_off, file_meta_sz)
    data_base = lvl3 + file_data_off
    invalid = 0xFFFFFFFF

    def dir_at(off: int):
        parent, sibling, child, first_file, hash_next, name_len = struct.unpack_from("<6I", dir_meta, off)
        name = dir_meta[off + 0x18 : off + 0x18 + name_len].decode("utf-16-le", "replace")
        return sibling, child, first_file, name

    def file_at(off: int):
        parent, sibling, doff, dsize, hash_next, name_len = struct.unpack_from("<IIQQII", file_meta, off)
        name = file_meta[off + 0x20 : off + 0x20 + name_len].decode("utf-16-le", "replace")
        return sibling, doff, dsize, name

    out: list[RomFsFile] = []

    def walk(dir_off: int, path: str) -> None:
        sibling, child, first_file, _name = dir_at(dir_off)
        fo = first_file
        while fo != invalid:
            fsib, doff, dsize, fname = file_at(fo)
            out.append(RomFsFile(path=f"{path}/{fname}", offset=data_base + doff, size=dsize))
            fo = fsib
        co = child
        while co != invalid:
            csib, _cchild, _cff, cname = dir_at(co)
            walk(co, f"{path}/{cname}")
            co = csib

    walk(0, "")
    return out


# -- GARC ------------------------------------------------------------------

def is_garc(image: ThreedsImage, offset: int) -> bool:
    return image.read(offset, 4) == b"CRAG"


def parse_garc(image: ThreedsImage, offset: int) -> list[GarcSubFile]:
    """Parse a GARC (v4/v6) archive header and return its sub-file table."""
    head = image.read(offset, 0x28)
    if head[:4] != b"CRAG":
        raise ValueError("not a GARC archive")
    header_size = struct.unpack_from("<I", head, 4)[0]
    version = struct.unpack_from("<H", head, 10)[0]

    fato_off = offset + header_size
    fato = image.read(fato_off, 0x0C)
    if fato[:4] != b"OTAF":
        raise ValueError("GARC FATO magic missing")
    fato_size = struct.unpack_from("<I", fato, 4)[0]
    count = struct.unpack_from("<H", fato, 8)[0]

    fatb_off = fato_off + fato_size
    fatb = image.read(fatb_off, 0x0C)
    if fatb[:4] != b"BTAF":
        raise ValueError("GARC FATB magic missing")
    fatb_size = struct.unpack_from("<I", fatb, 4)[0]
    file_count = struct.unpack_from("<I", fatb, 8)[0]

    # FIMB (data) chunk follows FATB.
    fimb_off = fatb_off + fatb_size
    fimb = image.read(fimb_off, 0x0C)
    data_start = fimb_off + 0x0C
    if fimb[:4] != b"BMIF":
        # Some builds pad; fall back to header dataOffset field.
        data_start = offset + struct.unpack_from("<I", head, 0x10)[0]

    entries_raw = image.read(fatb_off + 0x0C, fatb_size - 0x0C)
    subfiles: list[GarcSubFile] = []
    pos = 0
    for index in range(file_count):
        flags = struct.unpack_from("<I", entries_raw, pos)[0]
        pos += 4
        # For each set bit in flags, a (start, end, length) triple follows.
        # USUM model GARC uses a single sub-entry per slot (flags == 1).
        chosen_start = None
        chosen_len = 0
        for bit in range(32):
            if not (flags >> bit) & 1:
                continue
            start, end, length = struct.unpack_from("<III", entries_raw, pos)
            pos += 12
            if chosen_start is None:
                chosen_start = start
                chosen_len = length
        if chosen_start is None:
            continue
        subfiles.append(
            GarcSubFile(index=index, offset=data_start + chosen_start, size=chosen_len)
        )
    return subfiles
