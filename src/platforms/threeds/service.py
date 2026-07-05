"""On-demand decode services for 3DS descriptor assets (preview + export)."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Callable, Iterable

import struct

from .bflim import decode_bflim
from .gf import (
    GFMODEL_MAGIC,
    GFTEXTURE_MAGIC,
    GfModel,
    GfTexture,
    is_gf_model,
    is_gf_texture,
    parse_gf_model,
    parse_gf_texture,
)
from .glb import FormVariantExport, write_model_glb
from .motion import GfMotion, is_gf_motion, parse_gf_motion
from .rom import (
    ANIMATION_SLOTS,
    MODEL_GROUP_STRIDE,
    POKEMON_MODEL_GARC,
    SLOT_MODEL,
    SLOT_TEXTURES_EXTRA,
    SLOT_TEXTURES_NORMAL,
    SLOT_TEXTURES_SHINY,
    parse_model_header_table,
    read_garc_slot,
    read_pack_entries,
)

Progress = Callable[[str], None]

_GFMODEL_MAGIC_BYTES = struct.pack("<I", GFMODEL_MAGIC)
_GFTEXTURE_MAGIC_BYTES = struct.pack("<I", GFTEXTURE_MAGIC)


def _safe_stem(descriptor: dict) -> str:
    species = int(descriptor.get("species") or 0)
    form = int(descriptor.get("form") or 0)
    if species:
        name = str(descriptor.get("name") or "").split(" (")[0]
        clean = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
        return f"pm{species:04d}_{form:02d}_{clean}" if clean else f"pm{species:04d}_{form:02d}"
    if descriptor.get("type") in ("world_model", "texture_bank"):
        garc = str(descriptor.get("garc") or "").strip("/").replace("/", "_")
        return f"w_{garc}_{int(descriptor.get('slot') or 0):04d}"
    return f"group{int(descriptor.get('group') or 0):04d}"


def _slot_context(descriptor: dict) -> str:
    return f"GARC {descriptor.get('garc')} slot {descriptor.get('slot')}"


def _find_magic_offsets(data: bytes, magic: bytes) -> list[int]:
    """4-byte-aligned occurrences of *magic* in *data* (GFModelPack scan)."""
    out: list[int] = []
    pos = data.find(magic)
    while pos != -1:
        if pos % 4 == 0:
            out.append(pos)
        pos = data.find(magic, pos + 4)
    return out


def _world_payload_chunks(payload: bytes) -> list[bytes]:
    """Split a world GARC subfile into candidate chunks: the payload itself
    plus (recursively, one level) the entries of a Game Freak 2-letter pack."""
    from .rom import parse_generic_pack_offsets

    chunks = [payload]
    entries = parse_generic_pack_offsets(payload)
    if entries:
        for start, end in entries:
            if end > start and end <= len(payload):
                chunk = payload[start:end]
                chunks.append(chunk)
                inner = parse_generic_pack_offsets(chunk)
                if inner:
                    for istart, iend in inner:
                        if iend > istart and iend <= len(chunk):
                            chunks.append(chunk[istart:iend])
    return chunks


def load_model(descriptor: dict) -> GfModel:
    if descriptor.get("type") == "world_model":
        return _load_world_model(descriptor)
    base = int(descriptor["base_slot"])
    package = read_garc_slot(descriptor["rom"], descriptor["garc"], base + SLOT_MODEL)
    entries = read_pack_entries(package)
    for entry in entries:
        if is_gf_model(entry):
            return parse_gf_model(entry, _safe_stem(descriptor))
    raise ValueError(
        f"no GFModel found in model package ({descriptor.get('garc')} slot {base + SLOT_MODEL})"
    )


def _load_world_model(descriptor: dict) -> GfModel:
    payload = read_garc_slot(descriptor["rom"], descriptor["garc"], int(descriptor["slot"]))
    for chunk in _world_payload_chunks(payload):
        if is_gf_model(chunk):
            return parse_gf_model(chunk, _safe_stem(descriptor))
        # GFModelPack container: the GFModel section sits at an aligned offset.
        if len(chunk) >= 4 and struct.unpack_from("<I", chunk)[0] == 0x00010000:
            for offset in _find_magic_offsets(chunk, _GFMODEL_MAGIC_BYTES):
                try:
                    return parse_gf_model(chunk[offset:], _safe_stem(descriptor))
                except Exception:
                    continue
    raise ValueError(f"{_slot_context(descriptor)}: no parseable GFModel in payload")


def _world_textures(descriptor: dict) -> list[GfTexture]:
    payload = read_garc_slot(descriptor["rom"], descriptor["garc"], int(descriptor["slot"]))
    if is_gf_texture(payload):
        try:
            return [parse_gf_texture(payload)]
        except ValueError as exc:
            raise ValueError(f"{_slot_context(descriptor)}: {exc}") from exc
    textures: list[GfTexture] = []
    seen: set[str] = set()
    # World payloads bury GFTexture sections inside packs / GFModelPacks;
    # find them by magic and skip 0-format dummy entries.
    for offset in _find_magic_offsets(payload, _GFTEXTURE_MAGIC_BYTES):
        try:
            texture = parse_gf_texture(payload[offset:])
        except ValueError:
            continue
        if texture.name in seen:
            continue
        seen.add(texture.name)
        textures.append(texture)
    return textures


# World model textures (field maps, buildings, battle intros) live in shared
# texture GARCs, not next to the model. These are the USUM GARCs that carry
# GFTexture sections.
_WORLD_TEXTURE_GARCS = (
    "/a/0/8/1",
    "/a/0/8/2",
    "/a/0/8/3",
    "/a/0/9/4",
    "/a/0/9/5",
    "/a/0/9/6",
    "/a/0/9/7",
    "/a/0/9/8",
)


def _world_texture_index_path(rom: Path) -> Path:
    from ...install import project_root

    stat = rom.stat()
    key = hashlib.sha1(
        f"{rom.resolve()}|{stat.st_size}|{int(stat.st_mtime)}".encode()
    ).hexdigest()[:16]
    return project_root() / "texture_index" / f"threeds_world_textures_{key}.json"


def _build_world_texture_index(rom: Path, progress: Progress | None = None) -> dict[str, list]:
    """Map texture name -> [garc_path, slot, byte_offset] across the shared
    world-texture GARCs. One-time scan, persisted next to other RAE indexes."""
    from .container import ThreedsImage, parse_garc
    from .lz11 import maybe_decompress

    index: dict[str, list] = {}
    with ThreedsImage(rom) as image:
        files = {f.path: f for f in image.romfs_files()}
        for garc_path in _WORLD_TEXTURE_GARCS:
            entry = files.get(garc_path)
            if entry is None or image.read(entry.offset, 4) != b"CRAG":
                continue
            try:
                subfiles = parse_garc(image, entry.offset)
            except Exception:
                continue
            if progress:
                progress(f"3DS: indexing world textures in {garc_path} ({len(subfiles)} slots)…")
            for sub in subfiles:
                if sub.size == 0:
                    continue
                try:
                    payload = maybe_decompress(image.read(sub.offset, sub.size))
                except Exception:
                    continue
                for offset in _find_magic_offsets(payload, _GFTEXTURE_MAGIC_BYTES):
                    try:
                        texture = parse_gf_texture(payload[offset:])
                    except Exception:
                        continue
                    # First hit wins; textures are duplicated across zone slots.
                    index.setdefault(texture.name, [garc_path, sub.index, offset])
    return index


def _world_texture_index(rom: Path, progress: Progress | None = None) -> dict[str, list]:
    cache_path = _world_texture_index_path(rom)
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text())
            if isinstance(cached, dict):
                return cached
        except Exception:
            pass
    index = _build_world_texture_index(rom, progress)
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(index))
    except Exception:
        pass
    return index


def resolve_world_textures(
    descriptor: dict,
    names: Iterable[str],
    progress: Progress | None = None,
) -> list[GfTexture]:
    """Fetch textures by name from the shared world-texture GARCs (used when a
    world model references sheets that are not stored in its own slot)."""
    wanted = {n for n in names if n}
    if not wanted:
        return []
    rom = Path(descriptor["rom"])
    index = _world_texture_index(rom, progress)
    slot_cache: dict[tuple[str, int], bytes] = {}
    out: list[GfTexture] = []
    for name in sorted(wanted):
        entry = index.get(name)
        if not entry:
            continue
        garc_path, slot, offset = str(entry[0]), int(entry[1]), int(entry[2])
        key = (garc_path, slot)
        if key not in slot_cache:
            try:
                slot_cache[key] = read_garc_slot(rom, garc_path, slot)
            except Exception:
                slot_cache[key] = b""
        payload = slot_cache[key]
        if not payload or offset >= len(payload):
            continue
        try:
            out.append(parse_gf_texture(payload[offset:]))
        except Exception:
            continue
    return out


def load_textures(descriptor: dict, *, shiny: bool = False) -> list[GfTexture]:
    if descriptor.get("type") in ("world_model", "texture_bank"):
        return _world_textures(descriptor)
    base = int(descriptor["base_slot"])
    slot = base + (SLOT_TEXTURES_SHINY if shiny else SLOT_TEXTURES_NORMAL)
    package = read_garc_slot(descriptor["rom"], descriptor["garc"], slot)
    textures: list[GfTexture] = []
    for entry in read_pack_entries(package):
        if is_gf_texture(entry):
            try:
                textures.append(parse_gf_texture(entry))
            except ValueError:
                continue
    # Some forms keep extra maps (eye blinks, pattern variants) in slot +3.
    if not textures:
        try:
            package = read_garc_slot(descriptor["rom"], descriptor["garc"], base + SLOT_TEXTURES_EXTRA)
            for entry in read_pack_entries(package):
                if is_gf_texture(entry):
                    textures.append(parse_gf_texture(entry))
        except Exception:
            pass
    return textures


def load_motions(descriptor: dict) -> list[GfMotion]:
    """Parse every GFMotion in the species' animation slots (+4..+7).

    Duplicate motions (the same bytes reappear across slots) are dropped.
    Failures on individual motions are skipped — the ROM data varies and a
    bad motion should never block model export.
    """
    if "base_slot" not in descriptor:
        return []
    base = int(descriptor["base_slot"])
    motions: list[GfMotion] = []
    seen: set[bytes] = set()
    for offset in ANIMATION_SLOTS:
        try:
            payload = read_garc_slot(descriptor["rom"], descriptor["garc"], base + offset)
        except Exception:
            continue
        for index, entry in enumerate(read_pack_entries(payload)):
            if not entry or not is_gf_motion(entry):
                continue
            digest = struct.pack("<I", len(entry)) + entry[:64] + entry[-64:]
            if digest in seen:
                continue
            seen.add(digest)
            try:
                motions.append(parse_gf_motion(entry, f"slot{offset}_{index:02d}"))
            except Exception:
                continue
    return motions


def _pokemon_species_form_descriptors(descriptor: dict) -> list[dict]:
    species = int(descriptor.get("species") or 0)
    if not species or descriptor.get("garc") != POKEMON_MODEL_GARC:
        return [descriptor]
    try:
        header = read_garc_slot(descriptor["rom"], descriptor["garc"], 0)
        table = parse_model_header_table(header)
    except Exception:
        return [descriptor]
    if species < 1 or species > len(table):
        return [descriptor]
    base_group, form_count, _flags = table[species - 1]
    if form_count <= 1:
        return [descriptor]
    clean_name = str(descriptor.get("name") or "").split(" (")[0]
    forms: list[dict] = []
    for form in range(form_count):
        group = int(base_group) + form
        forms.append(
            {
                **descriptor,
                "type": "model",
                "group": group,
                "base_slot": 1 + group * MODEL_GROUP_STRIDE,
                "form": form,
                "name": clean_name or descriptor.get("name") or f"species {species}",
            }
        )
    return forms


def _model_geometry_fingerprint(model: GfModel) -> str:
    digest = hashlib.sha1()
    for mesh in sorted(model.meshes, key=lambda m: m.name):
        digest.update(mesh.name.encode("utf-8", "replace"))
        for sub in sorted(mesh.submeshes, key=lambda s: s.material_name):
            digest.update(sub.material_name.encode("utf-8", "replace"))
            digest.update(struct.pack("<II", len(sub.positions), len(sub.indices)))
            for pos in sub.positions:
                digest.update(struct.pack("<3f", *pos))
            digest.update(struct.pack(f"<{len(sub.indices)}I", *sub.indices) if sub.indices else b"")
    return digest.hexdigest()


def build_model_glb(
    descriptor: dict,
    out_dir: str | Path,
    *,
    shiny: bool = False,
    progress: Progress | None = None,
) -> Path:
    """Write one GLB with normal textures; Pokémon rows also embed a shiny set."""
    out_dir = Path(out_dir)
    form_descriptors = _pokemon_species_form_descriptors(descriptor)
    selected_form = int(descriptor.get("form") or 0)
    base_descriptor = next(
        (item for item in form_descriptors if int(item.get("form") or 0) == selected_form),
        form_descriptors[0],
    )
    stem = _safe_stem(base_descriptor)
    if progress:
        progress(f"3DS: parsing GFModel for {stem}…")
    model = load_model(base_descriptor)
    if progress:
        progress("3DS: decoding textures…")
    textures = load_textures(base_descriptor, shiny=False)
    shiny_textures: list[GfTexture] | None = None
    if base_descriptor.get("type") not in ("world_model", "texture_bank") and base_descriptor.get("species"):
        try:
            shiny_textures = load_textures(base_descriptor, shiny=True)
        except Exception:
            shiny_textures = None
    form_exports: list[FormVariantExport] = []
    if len(form_descriptors) > 1:
        base_fingerprint = _model_geometry_fingerprint(model)
        for form_descriptor in form_descriptors:
            form_id = f"{int(form_descriptor.get('form') or 0):02d}"
            if int(form_descriptor.get("form") or 0) == selected_form:
                continue
            try:
                form_model = load_model(form_descriptor)
                form_textures = load_textures(form_descriptor, shiny=False)
            except Exception:
                if progress:
                    progress(f"3DS: skipping form {form_id}; model or textures failed to decode")
                continue
            try:
                form_shiny = load_textures(form_descriptor, shiny=True)
            except Exception:
                form_shiny = None
            geometry = (
                "texture_only"
                if _model_geometry_fingerprint(form_model) == base_fingerprint
                else "full_geometry"
            )
            form_exports.append(
                FormVariantExport(
                    id=form_id,
                    label=f"Form {form_id}",
                    model=form_model,
                    textures=form_textures,
                    shiny_textures=form_shiny,
                    geometry=geometry,
                )
            )
        if progress and form_exports:
            texture_only = sum(1 for form in form_exports if form.geometry == "texture_only")
            progress(
                f"3DS: bundling {len(form_exports) + 1} form(s) "
                f"({texture_only} texture-only, {len(form_exports) - texture_only} geometry)"
            )
    if base_descriptor.get("type") == "world_model":
        have = {t.name for t in textures}
        need = {
            name
            for mat in model.materials
            for name in mat.texture_names
            if name and "dummy" not in name.lower()
        }
        missing = need - have
        if missing:
            if progress:
                progress(f"3DS: resolving {len(missing)} shared world texture(s)…")
            textures.extend(resolve_world_textures(base_descriptor, missing, progress))
    if progress:
        progress("3DS: parsing GFMotion animations…")
    try:
        motions = load_motions(base_descriptor)
    except Exception:
        motions = []
    out_path = out_dir / f"{stem}.glb"
    if progress:
        progress(f"3DS: writing GLB {out_path.name} ({len(motions)} animation(s))…")
    return write_model_glb(
        model,
        textures,
        out_path,
        animations=motions,
        shiny_textures=shiny_textures,
        default_texture_variant="shiny" if shiny else "normal",
        default_form_variant=f"{selected_form:02d}",
        form_variants=form_exports,
    )


def export_texture_pngs(
    descriptor: dict,
    out_dir: str | Path,
    *,
    progress: Progress | None = None,
) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = _safe_stem(descriptor)
    written: list[Path] = []
    variants = (
        ((False, "normal"),)
        if descriptor.get("type") in ("world_model", "texture_bank")
        else ((False, "normal"), (True, "shiny"))
    )
    for shiny, label in variants:
        textures = load_textures(descriptor, shiny=shiny)
        if progress:
            progress(f"3DS: decoding {len(textures)} {label} texture(s)…")
        for tex in textures:
            clean = tex.name.replace(".tga", "").replace("/", "_") or "texture"
            path = out_dir / f"{stem}_{label}_{clean}.png"
            try:
                path.write_bytes(tex.to_png())
            except Exception:
                continue
            written.append(path)
    return written


def export_animation_payloads(
    descriptor: dict,
    out_dir: str | Path,
) -> list[Path]:
    """Dump the raw GFMotion packs (no converter yet — raw research payloads)."""
    if "base_slot" not in descriptor:
        return []  # world/texture rows have no animation slot group
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    base = int(descriptor["base_slot"])
    stem = _safe_stem(descriptor)
    written: list[Path] = []
    for offset in ANIMATION_SLOTS:
        try:
            payload = read_garc_slot(descriptor["rom"], descriptor["garc"], base + offset)
        except Exception:
            continue
        if not payload:
            continue
        path = out_dir / f"{stem}_motion_{offset}.bin"
        path.write_bytes(payload)
        written.append(path)
    return written


def decode_sprite_png(descriptor: dict) -> bytes:
    index = int(descriptor["sprite_index"])
    payload = read_garc_slot(descriptor["rom"], descriptor["garc"], index)
    try:
        return decode_bflim(payload).to_png()
    except ValueError as exc:
        raise ValueError(f"GARC {descriptor.get('garc')} slot {index}: {exc}") from exc


def texture_summary(descriptor: dict) -> dict:
    """Small report used by the details pane."""
    out: dict = {"normal": [], "shiny": []}
    for shiny, key in ((False, "normal"), (True, "shiny")):
        try:
            textures = load_textures(descriptor, shiny=shiny)
        except Exception:
            continue
        out[key] = [
            {"name": t.name, "width": t.width, "height": t.height, "format": f"0x{t.gf_format:02X}"}
            for t in textures
        ]
    return out
