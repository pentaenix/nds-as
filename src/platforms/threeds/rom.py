"""Nintendo 3DS ROM scan (decrypted .cci/.3ds/.cxi).

Emits lightweight descriptor rows (JSON payloads) — model/texture/sprite data
is re-read from the ROM lazily by ``service.py`` at preview/export time so a
1.3 GB Pokémon GARC never has to live in memory.
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Callable

from ...core.assets import Asset
from .container import GarcSubFile, ThreedsCryptoError, ThreedsImage, parse_garc
from .gf import GFMODEL_MAGIC as GFMODEL_MAGIC_U32
from .gf import GFTEXTURE_MAGIC as GFTEXTURE_MAGIC_U32
from .game_catalog import identify_threeds_game
from .lz11 import maybe_decompress
from .named_romfs import CGFX_MAGICS, scan_named_romfs_assets
from .species import species_name
from .world_composition import (
    BATTLE_BACKGROUND_GARC,
    apply_composition,
    compositions_for_slot,
    default_composition_for_slot,
)

POKEMON_MODEL_GARC = "/a/0/9/4"
POKEMON_ICON_GARC = "/a/0/6/2"

# Game Freak "GFModelPack" container magic (u32 LE) — wraps GFModel + GFTexture
# sections for world/battle/character models.
GF_MODELPACK_MAGIC = 0x00010000

# Friendly virtual-tree groups for GARCs discovered empirically in USUM.
# Everything else with model/texture/sprite content lands under other/<garc>.
WORLD_GARC_GROUPS: dict[str, tuple[str, str]] = {
    "/a/0/8/1": ("world/battle_backgrounds", "Battle background"),
    "/a/0/8/5": ("world/maps", "Field map model"),
    "/a/0/9/5": ("world/battle_effects", "Battle effect model"),
    "/a/1/7/4": ("characters/battle", "Trainer model (battle)"),
    "/a/2/0/0": ("characters/field", "Character model (field)"),
    "/a/2/4/4": ("textures/pokedex_pictures", "Pokédex picture"),
    "/a/2/5/7": ("textures/pokemon_icons", "Pokémon icon texture"),
    "/a/0/6/1": ("sprites/icons_alt", "Menu icon sprite (alt)"),
    "/a/1/3/8": ("sprites/ui_1_3_8", "UI sprite"),
    "/a/1/5/8": ("sprites/ui_1_5_8", "UI sprite"),
    "/a/1/6/3": ("sprites/ui_1_6_3", "UI sprite"),
    "/a/2/7/3": ("sprites/ui_2_7_3", "UI sprite"),
}

# Byte-identical duplicates of other GARCs (verified against USUM) — skip so
# the tree doesn't show every asset twice.
WORLD_GARC_SKIP = {"/a/0/8/6", "/a/0/9/6", "/a/0/9/7"}

# Scan-time sampling limits: read at most this much raw data per subfile and
# stop LZ11 decompression once this much output is available.
_PEEK_RAW_LIMIT = 0x10000
_PEEK_OUT_LIMIT = 0x8000

# Per-species-form slot group inside the model GARC (Sun/Moon/USUM layout):
# +0 model package, +1 normal textures, +2 shiny textures, +3 extra textures,
# +4..+7 animation packs, +8 misc.
MODEL_GROUP_STRIDE = 9
SLOT_MODEL = 0
SLOT_TEXTURES_NORMAL = 1
SLOT_TEXTURES_SHINY = 2
SLOT_TEXTURES_EXTRA = 3
ANIMATION_SLOTS = (4, 5, 6, 7)


def _lz11_prefix(data: bytes, out_limit: int) -> bytes | None:
    """Decompress the first *out_limit* bytes of an LZ11 stream (header-only
    sampling at scan time; the full stream may be truncated)."""
    if len(data) < 4 or data[0] != 0x11:
        return None
    out_size = data[1] | data[2] << 8 | data[3] << 16
    pos = 4
    if out_size == 0:
        if len(data) < 8:
            return None
        out_size = int.from_bytes(data[4:8], "little")
        pos = 8
    limit = min(out_size, out_limit)
    out = bytearray()
    total = len(data)
    try:
        while len(out) < limit:
            flags = data[pos]
            pos += 1
            for bit in range(8):
                if len(out) >= limit:
                    break
                if not (flags >> (7 - bit)) & 1:
                    out.append(data[pos])
                    pos += 1
                    continue
                b1 = data[pos]
                indicator = b1 >> 4
                if indicator == 0:
                    b2, b3 = data[pos + 1], data[pos + 2]
                    pos += 3
                    length = ((b1 & 0xF) << 4 | b2 >> 4) + 0x11
                    disp = ((b2 & 0xF) << 8 | b3) + 1
                elif indicator == 1:
                    b2, b3, b4 = data[pos + 1], data[pos + 2], data[pos + 3]
                    pos += 4
                    length = ((b1 & 0xF) << 12 | b2 << 4 | b3 >> 4) + 0x111
                    disp = ((b3 & 0xF) << 8 | b4) + 1
                else:
                    b2 = data[pos + 1]
                    pos += 2
                    length = (b1 >> 4) + 1
                    disp = ((b1 & 0xF) << 8 | b2) + 1
                if disp > len(out):
                    return None
                length = min(length, limit - len(out))
                if length <= disp:
                    start = len(out) - disp
                    out.extend(out[start : start + length])
                else:
                    # Overlapping back-reference: periodic repeat of the last
                    # *disp* bytes.
                    segment = bytes(out[-disp:])
                    repeats = (length + disp - 1) // disp
                    out.extend((segment * repeats)[:length])
                if pos > total:
                    break
    except IndexError:
        pass  # truncated raw prefix — return what we decoded so far
    return bytes(out)


def parse_generic_pack_offsets(data: bytes) -> list[tuple[int, int]] | None:
    """Offset table of a Game Freak 2-letter pack ('BG', 'CM', 'EM', 'PC'…).

    Returns ``[(start, end), …]`` per entry, or ``None`` when *data* does not
    look like a pack. Works on a payload prefix: entries whose offsets lie
    beyond ``len(data)`` are still reported (their bytes just aren't here).
    """
    if len(data) < 8:
        return None
    if not (0x41 <= data[0] <= 0x5A and 0x41 <= data[1] <= 0x5A):
        return None
    count = struct.unpack_from("<H", data, 2)[0]
    header_size = 4 + 4 * (count + 1)
    if count == 0 or count > 0x4000 or len(data) < header_size:
        return None
    offsets = struct.unpack_from(f"<{count + 1}I", data, 4)
    if offsets[0] < header_size:
        return None
    prev = 0
    for offset in offsets:
        if offset < prev:
            return None
        prev = offset
    return [(offsets[i], offsets[i + 1]) for i in range(count)]


def classify_garc_payload(data: bytes, *, complete: bool = True) -> str:
    """Classify one decompressed GARC subfile payload (or prefix of one).

    Returns ``"empty"``, ``"gfmodel"``, ``"gftexture"``, ``"bflim"``,
    ``"model_pack"``, ``"texture_pack"`` or ``"unknown"``. Set
    ``complete=False`` when *data* is only a prefix (BFLIM detection needs the
    file footer, so it is skipped for prefixes).
    """
    if len(data) < 4:
        return "empty" if not data else "unknown"
    magic = struct.unpack_from("<I", data)[0]
    if magic == GFMODEL_MAGIC_U32:
        return "gfmodel"
    if magic == GFTEXTURE_MAGIC_U32:
        return "gftexture"
    if magic == GF_MODELPACK_MAGIC:
        return "model_pack"
    if complete and len(data) > 0x28 and data[-0x28 : -0x28 + 4] == b"FLIM":
        return "bflim"
    entries = parse_generic_pack_offsets(data)
    if entries:
        has_texture = False
        for start, end in entries:
            if end <= start or start + 4 > len(data):
                continue
            inner = struct.unpack_from("<I", data, start)[0]
            if inner in (GFMODEL_MAGIC_U32, GF_MODELPACK_MAGIC):
                return "model_pack"
            if inner == GFTEXTURE_MAGIC_U32:
                has_texture = True
        if has_texture:
            return "texture_pack"
    return "unknown"


def read_pack_entries(data: bytes) -> list[bytes]:
    """Split a Game Freak 'PC' package into its sub-payloads."""
    if len(data) < 8 or data[:2] != b"PC":
        return []
    count = struct.unpack_from("<H", data, 2)[0]
    offsets = struct.unpack_from(f"<{count + 1}I", data, 4)
    return [data[offsets[i] : offsets[i + 1]] for i in range(count)]


def parse_model_header_table(data: bytes) -> list[tuple[int, int, int]]:
    """Header slot 0: per-species (base group, form count, flags)."""
    entries: list[tuple[int, int, int]] = []
    for i in range(len(data) // 4):
        entries.append(struct.unpack_from("<HBB", data, i * 4))
    return entries


def _descriptor_asset(
    *,
    asset_id: str,
    virtual_path: str,
    kind: str,
    magic: str,
    payload: dict,
    mapping_label: str,
    mapping_category: str = "3ds",
) -> Asset:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Asset(
        asset_id=asset_id,
        virtual_path=virtual_path,
        kind=kind,
        magic=magic,
        extension=".json",
        data=data,
        original_data=data,
        mapping_category=mapping_category,
        mapping_label=mapping_label,
        mapping_confidence="3ds-garc-layout",
    )


def scan_threeds_rom_path(
    path: str | Path,
    progress: Callable[[str], None] | None = None,
    **_kwargs,
) -> list[Asset]:
    source = Path(path).expanduser().resolve()

    def report(message: str) -> None:
        if progress:
            progress(message)

    report(f"3DS: opening {source.name}…")
    assets: list[Asset] = []
    with ThreedsImage(source) as image:
        part = image.main_partition()
        if part is None:
            raise ThreedsCryptoError(f"{source.name}: no NCCH partitions found")
        if part.encrypted:
            raise ThreedsCryptoError(
                f"{source.name}: NCCH partition is AES-encrypted. RAE only reads decrypted "
                "dumps (NoCrypto flag) — re-dump the cartridge with decryption enabled."
            )
        romfs_entries = image.romfs_files(part)
        files = {f.path: f for f in romfs_entries}
        report(f"3DS: RomFS parsed — {len(files)} files, product {part.product_code}")
        game_id = identify_threeds_game(part.product_code, romfs_paths=set(files))
        report(f"3DS: selected game profile {game_id}")

        summary_payload = {
            "type": "summary",
            "rom": str(source),
            "game": game_id,
            "product_code": part.product_code,
            "romfs_files": len(files),
            "model_garc": POKEMON_MODEL_GARC if POKEMON_MODEL_GARC in files else None,
            "icon_garc": POKEMON_ICON_GARC if POKEMON_ICON_GARC in files else None,
        }
        assets.append(
            _descriptor_asset(
                asset_id="threeds_rom_summary",
                virtual_path=f"3ds/{source.stem}/rae_3ds_rom.json",
                kind="3DS ROM summary",
                magic="3DSR",
                payload=summary_payload,
                mapping_label=f"{source.stem} — {part.product_code}",
            )
        )

        if game_id == "pokemon_ultra_moon":
            # Keep the verified USUM GARC pipeline isolated and unchanged.
            assets.extend(_scan_pokemon_models(image, files, source, report))
            assets.extend(_scan_pokemon_icons(image, files, source, report))
            assets.extend(_scan_world_garcs(image, files, source, report))
        else:
            assets.extend(
                scan_named_romfs_assets(
                    image,
                    romfs_entries,
                    rom_path=str(source),
                    rom_stem=source.stem,
                    product_code=part.product_code,
                    game_id=game_id,
                    report=report,
                )
            )

    report(f"3DS: scan complete — {len(assets)} rows")
    return assets


def _scan_pokemon_models(
    image: ThreedsImage,
    files: dict,
    source: Path,
    report: Callable[[str], None],
) -> list[Asset]:
    entry = files.get(POKEMON_MODEL_GARC)
    if entry is None:
        report("3DS: Pokémon model GARC (/a/0/9/4) not found — skipping models")
        return []
    subfiles = parse_garc(image, entry.offset)
    if not subfiles:
        return []
    header = maybe_decompress(image.read(subfiles[0].offset, subfiles[0].size))
    table = parse_model_header_table(header)
    group_count = (len(subfiles) - 1) // MODEL_GROUP_STRIDE

    # Table row N-1 describes species N: (base group, form count, flags).
    # Rows past the dex range hold unrelated data, so stop at species 807.
    species_by_group: dict[int, tuple[int, int]] = {}
    for number in range(1, min(len(table) + 1, 808)):
        base, count, _flags = table[number - 1]
        if count == 0:
            continue
        if base + count > group_count:
            break
        for form in range(count):
            species_by_group.setdefault(base + form, (number, form))

    assets: list[Asset] = []
    report(f"3DS: {group_count} Pokémon model groups in {POKEMON_MODEL_GARC}")
    for group in range(group_count):
        # Placeholder groups (shared-model gender forms, unused slots) hold a
        # 37-byte empty package — skip them instead of listing dead rows.
        model_slot = subfiles[1 + group * MODEL_GROUP_STRIDE]
        if model_slot.size < 0x100:
            continue
        species, form = species_by_group.get(group, (0, 0))
        if species:
            label = f"{species:04d} {species_name(species)}"
            folder = f"3ds/{source.stem}/pokemon/{label}"
            suffix = f"form_{form:02d}"
            display = f"{species_name(species)} (#{species:04d}, form {form:02d})"
        else:
            label = f"group_{group:04d}"
            folder = f"3ds/{source.stem}/pokemon/_ungrouped/{label}"
            suffix = "form_00"
            display = f"Model group {group:04d}"

        base_slot = 1 + group * MODEL_GROUP_STRIDE
        common = {
            "rom": str(source),
            "garc": POKEMON_MODEL_GARC,
            "group": group,
            "base_slot": base_slot,
            "species": species,
            "form": form,
            "name": display,
        }
        assets.append(
            _descriptor_asset(
                asset_id=f"threeds_model_{group:04d}",
                virtual_path=f"{folder}/{suffix}/model.gfmodel",
                kind="3DS Pokémon model",
                magic="GFMD",
                payload={**common, "type": "model"},
                mapping_label=f"{display} — GFModel package",
            )
        )
        assets.append(
            _descriptor_asset(
                asset_id=f"threeds_tex_{group:04d}",
                virtual_path=f"{folder}/{suffix}/textures.gftex",
                kind="3DS texture set (normal + shiny)",
                magic="GFTX",
                payload={**common, "type": "textures"},
                mapping_label=f"{display} — textures (normal + shiny)",
            )
        )
    return assets


def _scan_pokemon_icons(
    image: ThreedsImage,
    files: dict,
    source: Path,
    report: Callable[[str], None],
) -> list[Asset]:
    entry = files.get(POKEMON_ICON_GARC)
    if entry is None:
        report("3DS: icon GARC (/a/0/6/2) not found — skipping sprites")
        return []
    subfiles = parse_garc(image, entry.offset)
    assets: list[Asset] = []
    report(f"3DS: {len(subfiles)} menu icon sprites in {POKEMON_ICON_GARC}")
    for sub in subfiles:
        if sub.size == 0:
            continue
        assets.append(
            _descriptor_asset(
                asset_id=f"threeds_icon_{sub.index:04d}",
                virtual_path=f"3ds/{source.stem}/sprites/icons/icon_{sub.index:04d}.bflim",
                kind="3DS menu icon sprite",
                magic="FLIM",
                payload={
                    "type": "sprite",
                    "rom": str(source),
                    "garc": POKEMON_ICON_GARC,
                    "sprite_index": sub.index,
                },
                mapping_label=f"Menu icon sprite {sub.index:04d}",
            )
        )
    return assets


def _peek_subfile(image: ThreedsImage, sub: GarcSubFile) -> tuple[bytes, bool]:
    """Header-only sample of a GARC subfile: ``(payload, complete)``.

    Reads at most ``_PEEK_RAW_LIMIT`` raw bytes and decompresses at most
    ``_PEEK_OUT_LIMIT`` bytes of LZ11 output, so scanning stays fast even for
    multi-megabyte subfiles.
    """
    raw = image.read(sub.offset, min(sub.size, _PEEK_RAW_LIMIT))
    complete = len(raw) >= sub.size
    if raw[:1] == b"\x11":
        prefix = _lz11_prefix(raw, _PEEK_OUT_LIMIT)
        if prefix is not None:
            out_size = raw[1] | raw[2] << 8 | raw[3] << 16
            if out_size == 0 and len(raw) >= 8:
                out_size = int.from_bytes(raw[4:8], "little")
            return prefix, len(prefix) >= out_size
    return raw, complete


def _world_group_for(path: str) -> tuple[str, str]:
    known = WORLD_GARC_GROUPS.get(path)
    if known:
        return known
    return f"other/{path.strip('/').replace('/', '_')}", f"3DS asset ({path})"


def _scan_world_garcs(
    image: ThreedsImage,
    files: dict,
    source: Path,
    report: Callable[[str], None],
) -> list[Asset]:
    """Emit descriptor rows for every non-Pokémon GARC whose subfiles hold
    GFModel packages, GFTextures or BFLIM sprites (backgrounds, maps,
    trainers, UI sprites…)."""
    assets: list[Asset] = []
    garc_paths = [
        p
        for p in sorted(files)
        if p not in (POKEMON_MODEL_GARC, POKEMON_ICON_GARC)
        and p not in WORLD_GARC_SKIP
        and image.read(files[p].offset, 4) == b"CRAG"
    ]
    report(f"3DS: sampling {len(garc_paths)} additional GARCs for world assets…")
    kind_totals: dict[str, int] = {}
    for path in garc_paths:
        try:
            subfiles = parse_garc(image, files[path].offset)
        except Exception as exc:
            report(f"3DS: {path}: GARC parse failed ({exc}) — skipped")
            continue
        group, label = _world_group_for(path)
        garc_slug = path.strip("/").replace("/", "_")
        rows = 0
        for sub in subfiles:
            if sub.size == 0:
                continue
            payload, complete = _peek_subfile(image, sub)
            kind = classify_garc_payload(payload, complete=complete)
            if kind in ("gfmodel", "model_pack"):
                asset_kind, magic, ext, dtype = "3DS world model", "GFMD", ".gfmodel", "world_model"
            elif kind in ("gftexture", "texture_pack"):
                asset_kind, magic, ext, dtype = "3DS texture", "GFTX", ".gftex", "texture_bank"
            elif kind == "bflim":
                asset_kind, magic, ext, dtype = "3DS sprite", "FLIM", ".bflim", "sprite"
            else:
                continue
            display = f"{label} {sub.index:04d}"
            payload_dict = {
                "type": dtype,
                "rom": str(source),
                "garc": path,
                "slot": sub.index,
                "name": display,
            }
            if dtype == "world_model" and path == BATTLE_BACKGROUND_GARC:
                compositions = compositions_for_slot(sub.index)
                if compositions:
                    payload_dict["world_compositions"] = [
                        {
                            "id": item.id,
                            "label": item.label,
                            "slots": list(item.slots),
                            "outer_slot": item.outer_slot,
                            "confidence": item.confidence,
                        }
                        for item in compositions
                    ]
                    default_composition = default_composition_for_slot(sub.index)
                    if default_composition is not None:
                        payload_dict = apply_composition(payload_dict, default_composition)
            if dtype == "sprite":
                payload_dict["sprite_index"] = sub.index
            assets.append(
                _descriptor_asset(
                    asset_id=f"threeds_w_{garc_slug}_{sub.index:04d}",
                    virtual_path=f"3ds/{source.stem}/{group}/{sub.index:04d}{ext}",
                    kind=asset_kind,
                    magic=magic,
                    payload=payload_dict,
                    mapping_label=f"{display} — {path}",
                )
            )
            rows += 1
            kind_totals[kind] = kind_totals.get(kind, 0) + 1
        if rows:
            report(f"3DS: {path}: {rows} asset row(s) → {group}")
    report(f"3DS: world scan done — {sum(kind_totals.values())} rows {kind_totals}")
    return assets


def load_descriptor(asset: Asset) -> dict | None:
    """Parse the JSON descriptor payload of a 3DS asset row."""
    if asset.magic not in {"GFMD", "GFTX", "FLIM", "3DSR", *CGFX_MAGICS}:
        return None
    try:
        payload = json.loads(asset.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def read_garc_slot(rom: str | Path, garc_path: str, slot: int) -> bytes:
    """Read + decompress one GARC sub-file from *rom* on demand."""
    with ThreedsImage(rom) as image:
        files = {f.path: f for f in image.romfs_files()}
        entry = files.get(garc_path)
        if entry is None:
            raise FileNotFoundError(f"{garc_path} not present in {rom}")
        subfiles: list[GarcSubFile] = parse_garc(image, entry.offset)
        if not 0 <= slot < len(subfiles):
            raise IndexError(f"GARC slot {slot} out of range ({len(subfiles)})")
        sub = subfiles[slot]
        return maybe_decompress(image.read(sub.offset, sub.size))
