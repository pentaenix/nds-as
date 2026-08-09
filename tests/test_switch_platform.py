"""Switch platform tests (keyless paths + Trinity helpers)."""
from __future__ import annotations

import io
import struct
from pathlib import Path

import pytest

from rae.platforms.switch.container import parse_pfs0
from rae.platforms.switch.fnv import fnv1a64
from rae.platforms.switch.hash_cache import HashCache
from rae.platforms.switch.keys import _parse_keyfile, find_prod_keys
from rae.platforms.switch.rom import _classify, scan_switch_rom_path
from rae.platforms.switch.trinity import TrpfdIndex, parse_trpak


def test_switch_platform_registered():
    from rae.core.registry import all_platforms, platform_for_path

    platforms = all_platforms()
    assert "switch" in platforms
    switch = platforms["switch"]
    assert switch.status == "active"
    assert ".nsp" in switch.rom_extensions and ".xci" in switch.rom_extensions
    assert platform_for_path(Path("game.nsp"), platforms).id == "switch"


def test_switch_module_builder_registered():
    from rae.core.modules.platform_boundaries import PLATFORM_MODULE_BUILDERS
    from rae.platforms.switch.platform_modules import build_switch_modules

    assert "switch" in PLATFORM_MODULE_BUILDERS
    assert build_switch_modules().platform_id == "switch"


def _make_pfs0(files: dict[str, bytes]) -> bytes:
    names = b""
    name_offsets = []
    for name in files:
        name_offsets.append(len(names))
        names += name.encode() + b"\0"
    while len(names) % 16:
        names += b"\0"
    entry_table = b""
    data = b""
    for (name, blob), noff in zip(files.items(), name_offsets):
        entry_table += struct.pack("<QQII", len(data), len(blob), noff, 0)
        data += blob
    header = b"PFS0" + struct.pack("<III", len(files), len(names), 0)
    return header + entry_table + names + data


def test_parse_pfs0_reads_entries():
    blob = _make_pfs0({"a.nca": b"AAA", "b.cnmt.xml": b"<xml/>"})
    entries = parse_pfs0(io.BytesIO(blob))
    assert [e.name for e in entries] == ["a.nca", "b.cnmt.xml"]
    fh = io.BytesIO(blob)
    fh.seek(entries[0].offset)
    assert fh.read(entries[0].size) == b"AAA"


def test_parse_keyfile_ignores_comments_and_bad_lines():
    text = "header_key = 000102030405060708090a0b0c0d0e0f\n# comment\nbad line\nfoo=zz"
    keys = _parse_keyfile(text)
    assert keys["header_key"] == bytes(range(16))
    assert "foo" not in keys  # non-hex value dropped


def test_classify_trinity_paths():
    assert _classify("/pokemon/pm0001.trmdl")[1] == "TRMD"
    assert _classify("/deco/tree.bntx")[1] == "BNTX"
    assert _classify("/anim/walk.tranm")[1] == "TRAN"
    assert _classify("/anim/idle.gfbanm")[1] == "TRAN"
    assert _classify("/arc/data.trpfs")[1] == "TRPF"
    assert _classify("/audio/BGM.bnk")[1] == "WWSE"
    assert _classify("/misc/readme.txt")[1] == "SWFL"


def test_fnv1a64_matches_pokedocs_sample():
    h = fnv1a64("pokemon/data/pm0000/pm0000_00_00/pm0000_00_00.trmdl")
    assert h == 0x0EF91DEF394CA2B8


def test_hash_cache_parses_trpak_block(tmp_path: Path):
    text = "\n".join(
        [
            "@pokemondatapm0000pm0000_00_00pm0000_00_00.trmmt.trpak",
            "0x0EF91DEF394CA2B8 pokemon/data/pm0000/pm0000_00_00/pm0000_00_00.trmdl",
        ]
    )
    path = tmp_path / "hashes.txt"
    path.write_text(text)
    cache = HashCache.load(path)
    assert cache.resolve(0x0EF91DEF394CA2B8) == (
        "arc/pokemondatapm0000pm0000_00_00pm0000_00_00.trmmt.trpak",
        "pokemon/data/pm0000/pm0000_00_00/pm0000_00_00.trmdl",
    )


def test_resolve_trpak_path_uses_trpfd_when_cache_path_missing():
    from rae.platforms.switch.trinity import TrinityArchive

    inner = "pokemon/data/pm0123/pm0123_00_00/pm0123_00_00.trmdl"
    bad = "arc/pokemondatapm0123pm0123_00_00pm0123_00_00_00000_defaultwait01_loop.traef.trpak"
    good = "arc/pokemondatapm0123pm0123_00_00pm0123_00_00_base.trmdt.trpak"
    file_hash = fnv1a64(inner)
    archive = object.__new__(TrinityArchive)
    archive._file_hash_to_trpak = {file_hash: good}
    archive._trpfs_hash_set = {fnv1a64(good)}
    assert archive.resolve_trpak_path(inner, bad, file_hash=file_hash) == good


def test_scan_without_keys_returns_locked_row(tmp_path, monkeypatch):
    monkeypatch.delenv("RAE_SWITCH_PROD_KEYS", raising=False)
    # Point key search away from any real prod.keys by using an isolated home.
    monkeypatch.setattr(
        "rae.platforms.switch.keys._KEY_SEARCH_DIRS", (tmp_path / "nowhere",)
    )
    nsp = tmp_path / "Game.nsp"
    nsp.write_bytes(_make_pfs0({"x.nca": b"\0" * 32}))
    assert find_prod_keys(nsp) is None
    rows = scan_switch_rom_path(nsp)
    assert len(rows) == 1
    assert rows[0].magic == "SWLK"
    assert b"prod.keys" in rows[0].data
