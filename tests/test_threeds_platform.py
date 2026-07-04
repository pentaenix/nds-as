"""3DS platform island tests: format decoders + (optional) real-ROM integration."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rae.platforms.threeds.gf import GFMODEL_MAGIC, GFTEXTURE_MAGIC
from rae.platforms.threeds.lz11 import decompress_lz11, maybe_decompress
from rae.platforms.threeds.pica import decode_pica_texture, rgba_to_png
from rae.platforms.threeds.rom import (
    GF_MODELPACK_MAGIC,
    classify_garc_payload,
    parse_generic_pack_offsets,
    parse_model_header_table,
    read_pack_entries,
)
from rae.platforms.threeds.species import species_name

ROM = Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"


def test_species_names():
    assert species_name(1) == "Bulbasaur"
    assert species_name(54) == "Psyduck"
    assert species_name(807) == "Zeraora"
    assert species_name(9999) == "Species #9999"


def test_lz11_literal_roundtrip():
    payload = b"RAE3DS!!"
    stream = bytes([0x11, len(payload), 0, 0, 0x00]) + payload
    assert decompress_lz11(stream) == payload
    assert maybe_decompress(stream) == payload
    assert maybe_decompress(payload) == payload  # non-LZ data passes through


def test_pack_entry_split():
    blobs = [b"alpha", b"beta!!"]
    offsets = []
    cursor = 4 + 4 * (len(blobs) + 1)
    for blob in blobs:
        offsets.append(cursor)
        cursor += len(blob)
    offsets.append(cursor)
    package = b"PC" + struct.pack("<H", len(blobs)) + struct.pack(f"<{len(blobs) + 1}I", *offsets)
    package += b"".join(blobs)
    assert read_pack_entries(package) == blobs
    assert read_pack_entries(b"NOPE") == []


def test_model_header_table():
    rows = [(0, 1, 1), (1, 3, 7), (4, 1, 1)]
    data = b"".join(struct.pack("<HBB", *row) for row in rows)
    assert parse_model_header_table(data) == rows


def _generic_pack(magic: bytes, blobs: list[bytes]) -> bytes:
    header_size = 4 + 4 * (len(blobs) + 1)
    offsets, cursor = [], header_size
    for blob in blobs:
        offsets.append(cursor)
        cursor += len(blob)
    offsets.append(cursor)
    return (
        magic
        + struct.pack("<H", len(blobs))
        + struct.pack(f"<{len(blobs) + 1}I", *offsets)
        + b"".join(blobs)
    )


def test_generic_pack_offsets():
    pack = _generic_pack(b"BG", [b"alpha", b"beta!!"])
    entries = parse_generic_pack_offsets(pack)
    assert entries is not None
    starts = [pack[s:e] for s, e in entries]
    assert starts == [b"alpha", b"beta!!"]
    assert parse_generic_pack_offsets(b"\x00\x00\x00\x00") is None
    assert parse_generic_pack_offsets(b"") is None


def test_classify_garc_payload_synthetic():
    gfmodel = struct.pack("<I", GFMODEL_MAGIC) + b"\x00" * 12
    gftexture = struct.pack("<I", GFTEXTURE_MAGIC) + b"\x00" * 12
    modelpack = struct.pack("<I", GF_MODELPACK_MAGIC) + b"\x00" * 12

    assert classify_garc_payload(b"") == "empty"
    assert classify_garc_payload(b"\x00" * 16) == "unknown"
    assert classify_garc_payload(gfmodel) == "gfmodel"
    assert classify_garc_payload(gftexture) == "gftexture"
    assert classify_garc_payload(modelpack) == "model_pack"

    # BFLIM is identified by its footer, so only complete payloads qualify.
    bflim = b"\x00" * 32 + b"FLIM" + b"\x00" * 16 + b"imag" + b"\x00" * 16
    assert classify_garc_payload(bflim) == "bflim"
    assert classify_garc_payload(bflim, complete=False) == "unknown"

    # 2-letter packs classify by their entries' magics.
    assert classify_garc_payload(_generic_pack(b"BG", [gfmodel, gftexture])) == "model_pack"
    assert classify_garc_payload(_generic_pack(b"CM", [b"\x00" * 8, modelpack])) == "model_pack"
    assert classify_garc_payload(_generic_pack(b"EM", [b"\x00" * 8, gftexture])) == "texture_pack"
    assert classify_garc_payload(_generic_pack(b"WD", [b"\x00" * 8, b"\xff" * 8])) == "unknown"


def test_rgba5551_texture_decode_single_tile():
    # 8x8 tile of solid opaque red in RGBA5551 (r=31, g=0, b=0, a=1).
    pixel = (31 << 11) | 1
    data = struct.pack("<64H", *([pixel] * 64))
    rgba = decode_pica_texture(data, 8, 8, 2)
    assert rgba[:4] == bytes((255, 0, 0, 255))
    png = rgba_to_png(rgba, 8, 8)
    assert png.startswith(b"\x89PNG")


@pytest.mark.skipif(not ROM.is_file(), reason="Ultra Moon test ROM not present")
def test_ultra_moon_scan_and_model():
    from rae.platforms.threeds.rom import load_descriptor, scan_threeds_rom_path
    from rae.platforms.threeds.service import load_model, load_textures

    assets = scan_threeds_rom_path(ROM)
    models = [a for a in assets if a.magic == "GFMD"]
    sprites = [a for a in assets if a.magic == "FLIM"]
    assert len(models) > 1000
    assert len(sprites) > 1000

    psyduck = next(a for a in models if "0054 Psyduck" in a.virtual_path)
    descriptor = load_descriptor(psyduck)
    model = load_model(descriptor)
    assert model.bones and model.bones[0].name == "pm0054_00"
    assert model.meshes and all(s.positions for m in model.meshes for s in m.submeshes)

    normal = load_textures(descriptor)
    shiny = load_textures(descriptor, shiny=True)
    assert {t.name for t in normal} == {t.name for t in shiny}
    assert any("Body" in t.name for t in normal)


@pytest.mark.skipif(not ROM.is_file(), reason="Ultra Moon test ROM not present")
def test_torkoal_fire_geom_triangle_strip_exports():
    """Torkoal's fire VFX meshes use PICA triangle strips, not triangle lists."""
    from rae.platforms.threeds.rom import load_descriptor, scan_threeds_rom_path
    from rae.platforms.threeds.service import build_model_glb, load_model

    assets = scan_threeds_rom_path(ROM)
    torkoal = next(a for a in assets if "0324 Torkoal" in a.virtual_path)
    descriptor = load_descriptor(torkoal)
    model = load_model(descriptor)
    fire = next(m for m in model.meshes if "FireGeomASkin" in m.name)
    indices = fire.submeshes[0].indices
    assert len(indices) % 3 == 0
    assert len(indices) > len(fire.submeshes[0].positions)

    glb = build_model_glb(descriptor, ROM.parent / "exports" / "pytest_threeds_torkoal")
    assert glb.is_file() and glb.stat().st_size > 0


@pytest.mark.skipif(not ROM.is_file(), reason="Ultra Moon test ROM not present")
def test_ultra_moon_world_assets():
    """The scan surfaces non-Pokémon groups and their previews decode."""
    from rae.platforms.threeds.rom import load_descriptor, scan_threeds_rom_path
    from rae.platforms.threeds.service import load_model, load_textures

    assets = scan_threeds_rom_path(ROM)

    backgrounds = [a for a in assets if "/world/battle_backgrounds/" in a.virtual_path]
    maps = [a for a in assets if "/world/maps/" in a.virtual_path]
    trainers = [a for a in assets if "/characters/battle/" in a.virtual_path]
    dex_pics = [a for a in assets if "/textures/pokedex_pictures/" in a.virtual_path]
    assert len(backgrounds) > 100
    assert len(maps) > 500
    assert len(trainers) > 100
    assert len(dex_pics) > 1000
    assert all(a.magic == "GFMD" for a in backgrounds)

    # A battle background GFModelPack parses into a model with meshes and
    # its embedded textures decode.
    descriptor = load_descriptor(backgrounds[0])
    assert descriptor["type"] == "world_model"
    model = load_model(descriptor)
    assert model.meshes and any(s.positions for m in model.meshes for s in m.submeshes)
    textures = load_textures(descriptor)
    assert textures and textures[0].to_png().startswith(b"\x89PNG")

    # Pokémon sections are unchanged alongside the new groups.
    assert any("0054 Psyduck" in a.virtual_path for a in assets)
