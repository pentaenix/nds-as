"""Nintendo Switch container parsing: NSP/XCI -> NCA -> RomFS.

Self-contained inside the Nintendo Switch platform module. Requires the
caller-supplied ``prod.keys`` (see keys.py); RAE ships no Nintendo key material.

Crypto used:
  * NCA header: AES-128-XTS, 0x200 sectors, Nintendo (big-endian) tweak.
  * NCA sections: AES-128-CTR, per-section counter seeded from the media offset.

Only the Program NCA's RomFS is walked (that holds the game's Trinity packs).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

from .keys import SwitchKeys, SwitchKeyError


class SwitchContainerError(RuntimeError):
    pass


# -- AES-XTS (Nintendo tweak) -------------------------------------------------


def _xts_decrypt(data: bytes, crypt_key: bytes, tweak_key: bytes, sector_size: int, first_sector: int = 0) -> bytes:
    from Crypto.Cipher import AES

    crypt = AES.new(crypt_key, AES.MODE_ECB)
    tweak_ecb = AES.new(tweak_key, AES.MODE_ECB)
    out = bytearray()
    sector = first_sector
    for base in range(0, len(data), sector_size):
        chunk = data[base : base + sector_size]
        # Nintendo tweak = ECB(tweak_key, sector_number as 16-byte big-endian).
        tweak = bytearray(tweak_ecb.encrypt(sector.to_bytes(16, "big")))
        for off in range(0, len(chunk), 16):
            block = chunk[off : off + 16]
            pp = bytes(b ^ t for b, t in zip(block, tweak))
            cc = crypt.decrypt(pp)
            out.extend(c ^ t for c, t in zip(cc, tweak))
            # Multiply tweak by x in GF(2^128) (little-endian, poly 0x87).
            carry = 0
            for i in range(16):
                new_carry = tweak[i] >> 7
                tweak[i] = ((tweak[i] << 1) | carry) & 0xFF
                carry = new_carry
            if carry:
                tweak[0] ^= 0x87
        sector += 1
    return bytes(out)


def _ctr_decrypt(data: bytes, key: bytes, counter_iv: bytes) -> bytes:
    from Crypto.Cipher import AES
    from Crypto.Util import Counter

    ctr = Counter.new(128, initial_value=int.from_bytes(counter_iv, "big"))
    return AES.new(key, AES.MODE_CTR, counter=ctr).decrypt(data)


# -- PFS0 (NSP) ---------------------------------------------------------------


@dataclass(slots=True)
class PartitionEntry:
    name: str
    offset: int  # absolute byte offset in the container file
    size: int


def parse_pfs0(fh, base: int = 0) -> list[PartitionEntry]:
    fh.seek(base)
    header = fh.read(16)
    if header[:4] != b"PFS0":
        raise SwitchContainerError("Not a PFS0 (NSP) container")
    count, strtab_size, _ = struct.unpack_from("<III", header, 4)
    entry_table = fh.read(count * 24)
    strtab = fh.read(strtab_size)
    data_start = base + 16 + count * 24 + strtab_size
    entries: list[PartitionEntry] = []
    for i in range(count):
        off, size, name_off, _ = struct.unpack_from("<QQII", entry_table, i * 24)
        end = strtab.find(b"\0", name_off)
        name = strtab[name_off : end if end != -1 else None].decode("utf-8", "replace")
        entries.append(PartitionEntry(name=name, offset=data_start + off, size=size))
    return entries


# -- HFS0 (XCI secure partition) ---------------------------------------------


def parse_hfs0(fh, base: int) -> list[PartitionEntry]:
    fh.seek(base)
    header = fh.read(16)
    if header[:4] != b"HFS0":
        raise SwitchContainerError("Not an HFS0 partition")
    count, strtab_size, _ = struct.unpack_from("<III", header, 4)
    entry_table = fh.read(count * 64)
    strtab = fh.read(strtab_size)
    data_start = base + 16 + count * 64 + strtab_size
    entries: list[PartitionEntry] = []
    for i in range(count):
        off, size, name_off, _hash_size, _ = struct.unpack_from("<QQIIQ", entry_table, i * 64)
        # remaining 0x20 hash bytes ignored
        end = strtab.find(b"\0", name_off)
        name = strtab[name_off : end if end != -1 else None].decode("utf-8", "replace")
        entries.append(PartitionEntry(name=name, offset=data_start + off, size=size))
    return entries


# -- NCA ----------------------------------------------------------------------

_SECTION_ROMFS = 0
_NCA_HEADER_SIZE = 0xC00


@dataclass(slots=True)
class NcaSection:
    index: int
    media_offset: int  # absolute file offset of section start
    media_size: int
    fs_type: int       # 0 = RomFS, 1 = PartitionFS
    crypto_type: int   # 1 none, 2 XTS, 3 CTR, 4 BKTR
    ctr_prefix: bytes  # 8-byte generation prefix for CTR counter
    key: bytes
    romfs_data_offset: int = 0  # IVFC level-6 (data) offset within the section


class SwitchNca:
    """Decrypted view over a single NCA inside a container file."""

    def __init__(self, fh, base: int, size: int, keys: SwitchKeys):
        self._fh = fh
        self._base = base
        self._size = size
        self._keys = keys
        self.sections: list[NcaSection] = []
        self.content_type: int = -1
        self._parse_header()

    def _parse_header(self) -> None:
        self._fh.seek(self._base)
        enc = self._fh.read(_NCA_HEADER_SIZE)
        # XTS: header_key = data(crypt) key || tweak key.
        crypt_key = self._keys.header_key[0:16]
        tweak_key = self._keys.header_key[16:32]
        header = _xts_decrypt(enc, crypt_key, tweak_key, sector_size=0x200)
        magic = header[0x200:0x204]
        if magic not in (b"NCA3", b"NCA2"):
            raise SwitchContainerError(
                "NCA header did not decrypt (bad/incomplete prod.keys header_key)."
            )
        self.content_type = header[0x205]
        key_generation = max(header[0x206], header[0x220])
        key_index = header[0x207]
        rights_id = header[0x230:0x240]
        has_rights = any(rights_id)

        body_key = self._resolve_body_key(header, key_index, key_generation, has_rights, rights_id)

        # Section table: 4 entries of 0x10 at 0x240; section headers at 0x400.
        for i in range(4):
            start_block, end_block, _, _ = struct.unpack_from("<IIII", header, 0x240 + i * 0x10)
            if start_block == 0 and end_block == 0:
                continue
            fs_header = header[0x400 + i * 0x200 : 0x400 + (i + 1) * 0x200]
            # fs_header: [0x2] FsType (0=RomFS,1=PartitionFS), [0x4] EncryptionType.
            fs_type = fs_header[0x2]
            crypto_type = fs_header[0x4]
            media_offset = self._base + start_block * 0x200
            media_size = (end_block - start_block) * 0x200
            ctr_prefix = fs_header[0x140:0x148]
            # RomFS uses an IVFC hash tree (magic "IVFC" at fs_header+0x8); the
            # last level is the actual RomFS image. Capture its offset.
            romfs_data_offset = 0
            if fs_header[0x8:0xC] == b"IVFC":
                # 6 level headers (offset u64, size u64, block u32, pad u32) start
                # at 0x18; the data level is the one with the largest offset.
                for lvl in range(6):
                    off = struct.unpack_from("<Q", fs_header, 0x18 + lvl * 0x18)[0]
                    romfs_data_offset = max(romfs_data_offset, off)
            self.sections.append(
                NcaSection(
                    index=i,
                    media_offset=media_offset,
                    media_size=media_size,
                    fs_type=fs_type,
                    crypto_type=crypto_type,
                    ctr_prefix=ctr_prefix,
                    key=body_key,
                    romfs_data_offset=romfs_data_offset,
                )
            )

    def _resolve_body_key(self, header, key_index, key_generation, has_rights, rights_id) -> bytes:
        from Crypto.Cipher import AES

        if has_rights:
            # Personalised title: content decrypts with the ticket title key.
            title_key = self._keys.raw.get("titlekey_" + rights_id.hex())
            if title_key is None:
                raise SwitchKeyError(
                    "This NCA is title-key encrypted; add the title key to your "
                    "keys (titlekey_<rights_id>) or provide the NSP ticket."
                )
            gen = max(key_generation - 1, 0) if key_generation else 0
            title_kek = self._keys.get(f"titlekek_{gen:02x}")
            return AES.new(title_kek, AES.MODE_ECB).decrypt(title_key)

        # Standard crypto: decrypt the key area with the key-area key.
        kak = self._keys.key_area_key(key_index, key_generation)
        key_area = header[0x300:0x340]
        decrypted_area = AES.new(kak, AES.MODE_ECB).decrypt(key_area)
        # Key area holds 4 keys; index 2 is the section key for RomFS/CTR.
        return decrypted_area[0x20:0x30]

    def read_section(self, section: NcaSection, offset: int, size: int) -> bytes:
        """Read + decrypt *size* bytes at *offset* within the section body."""
        abs_off = section.media_offset + offset
        if section.crypto_type in (0, 1):
            self._fh.seek(abs_off)
            return self._fh.read(size)
        if section.crypto_type == 3:  # CTR
            # The AES-CTR keystream is aligned to the NCA base (self._base), which
            # may itself be unaligned inside the container file. Align on the
            # NCA-relative offset, not the raw file offset.
            nca_off = abs_off - self._base
            pad = nca_off & 0xF
            self._fh.seek(abs_off - pad)
            raw = self._fh.read(size + pad)
            counter = self._ctr_iv(section, nca_off - pad)
            return _ctr_decrypt(raw, section.key, counter)[pad : pad + size]
        raise SwitchContainerError(f"Unsupported NCA crypto type {section.crypto_type}")

    def _ctr_iv(self, section: NcaSection, nca_off: int) -> bytes:
        # Nintendo CTR: high 8 bytes = section ctr prefix (reversed), low 8 =
        # the NCA-relative block index (offset >> 4), big-endian.
        block = nca_off >> 4
        return section.ctr_prefix[::-1] + block.to_bytes(8, "big")


# -- RomFS --------------------------------------------------------------------


@dataclass(slots=True)
class RomFsFile:
    path: str
    offset: int  # offset within the section body (file-data region already added)
    size: int


def parse_romfs(nca: SwitchNca, section: NcaSection) -> list[RomFsFile]:
    """Walk the RomFS image (already located via the IVFC data level)."""
    romfs_base = section.romfs_data_offset
    meta = nca.read_section(section, romfs_base, 0x50)
    (
        _header_size,
        dir_hash_off,
        dir_hash_size,
        dir_meta_off,
        dir_meta_size,
        file_hash_off,
        file_hash_size,
        file_meta_off,
        file_meta_size,
        file_data_off,
    ) = struct.unpack_from("<QQQQQQQQQQ", meta, 0)
    dir_meta = nca.read_section(section, romfs_base + dir_meta_off, dir_meta_size)
    file_meta = nca.read_section(section, romfs_base + file_meta_off, file_meta_size)
    data_base = romfs_base + file_data_off

    files: list[RomFsFile] = []

    def dir_name(entry_off: int) -> tuple[int, int, str]:
        parent, sibling, child, file_child, _hash, name_len = struct.unpack_from(
            "<IIIIII", dir_meta, entry_off
        )
        name = dir_meta[entry_off + 0x18 : entry_off + 0x18 + name_len].decode("utf-8", "replace")
        return child, file_child, name

    def walk_dir(entry_off: int, prefix: str) -> None:
        _parent, _sibling, child, file_child, _hash, name_len = struct.unpack_from(
            "<IIIIII", dir_meta, entry_off
        )
        # files in this dir
        f = file_child
        while f != 0xFFFFFFFF and f + 0x20 <= len(file_meta):
            _fparent, fsibling, data_off, data_size, _fhash, fname_len = struct.unpack_from(
                "<IIQQII", file_meta, f
            )
            fname = file_meta[f + 0x20 : f + 0x20 + fname_len].decode("utf-8", "replace")
            files.append(
                RomFsFile(path=f"{prefix}/{fname}", offset=data_base + data_off, size=data_size)
            )
            f = fsibling
        # subdirectories
        d = child
        while d != 0xFFFFFFFF and d + 0x18 <= len(dir_meta):
            _dparent, dsibling, dchild, dfile, _dhash, dname_len = struct.unpack_from(
                "<IIIIII", dir_meta, d
            )
            dname = dir_meta[d + 0x18 : d + 0x18 + dname_len].decode("utf-8", "replace")
            walk_dir(d, f"{prefix}/{dname}")
            d = dsibling

    walk_dir(0, "")
    return files


# -- Top-level container ------------------------------------------------------


class SwitchRom:
    """Opens an NSP/XCI, exposes the Program NCA's RomFS files."""

    def __init__(self, path: str | Path, keys: SwitchKeys | None = None):
        self.path = Path(path).expanduser().resolve()
        self._fh = self.path.open("rb")
        self.keys = keys or SwitchKeys.load(self.path)
        self._program_nca: SwitchNca | None = None
        self._romfs_section: NcaSection | None = None
        self._romfs_files: list[RomFsFile] | None = None

    def close(self) -> None:
        try:
            self._fh.close()
        except Exception:
            pass

    def __enter__(self) -> "SwitchRom":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _partition_entries(self) -> list[PartitionEntry]:
        self._fh.seek(0)
        magic = self._fh.read(4)
        if magic == b"PFS0":
            return parse_pfs0(self._fh, 0)
        if magic == b"HEAD":
            # XCI: root HFS0 at the offset in the gamecard header (0x130).
            self._fh.seek(0x130)
            hfs0_off = struct.unpack("<Q", self._fh.read(8))[0]
            root = parse_hfs0(self._fh, hfs0_off)
            secure = next((e for e in root if e.name == "secure"), None)
            if secure is None:
                raise SwitchContainerError("XCI has no secure partition")
            return parse_hfs0(self._fh, secure.offset)
        raise SwitchContainerError(f"Unknown Switch container magic {magic!r}")

    def _load_program(self) -> None:
        if self._program_nca is not None:
            return
        entries = [e for e in self._partition_entries() if e.name.endswith(".nca")]
        # The Program NCA (content_type 0) is the large one; check biggest first.
        for entry in sorted(entries, key=lambda e: -e.size):
            try:
                nca = SwitchNca(self._fh, entry.offset, entry.size, self.keys)
            except SwitchContainerError:
                continue
            if nca.content_type != 0:  # 0 = Program
                continue
            romfs = next(
                (s for s in nca.sections if s.fs_type == _SECTION_ROMFS and s.crypto_type in (0, 1, 3)),
                None,
            )
            if romfs is None:
                continue
            self._program_nca = nca
            self._romfs_section = romfs
            return
        raise SwitchContainerError("No decryptable Program NCA with RomFS found")

    def romfs_files(self) -> list[RomFsFile]:
        if self._romfs_files is None:
            self._load_program()
            assert self._program_nca and self._romfs_section
            self._romfs_files = parse_romfs(self._program_nca, self._romfs_section)
        return self._romfs_files

    def read_file(self, romfs_file: RomFsFile, offset: int = 0, size: int | None = None) -> bytes:
        self._load_program()
        assert self._program_nca and self._romfs_section
        want = romfs_file.size - offset if size is None else min(size, romfs_file.size - offset)
        return self._program_nca.read_section(
            self._romfs_section, romfs_file.offset + offset, want
        )
