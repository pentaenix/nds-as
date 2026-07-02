"""On-demand decode services for 3DS descriptor assets (preview + export)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

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
from .glb import write_model_glb
from .motion import GfMotion, is_gf_motion, parse_gf_motion
from .rom import (
    ANIMATION_SLOTS,
    SLOT_MODEL,
    SLOT_TEXTURES_EXTRA,
    SLOT_TEXTURES_NORMAL,
    SLOT_TEXTURES_SHINY,
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


def build_model_glb(
    descriptor: dict,
    out_dir: str | Path,
    *,
    shiny: bool = False,
    progress: Progress | None = None,
) -> Path:
    out_dir = Path(out_dir)
    stem = _safe_stem(descriptor)
    suffix = "_shiny" if shiny else ""
    if progress:
        progress(f"3DS: parsing GFModel for {stem}…")
    model = load_model(descriptor)
    if progress:
        progress(f"3DS: decoding {'shiny ' if shiny else ''}textures…")
    textures = load_textures(descriptor, shiny=shiny)
    if progress:
        progress("3DS: parsing GFMotion animations…")
    try:
        motions = load_motions(descriptor)
    except Exception:
        motions = []
    out_path = out_dir / f"{stem}{suffix}.glb"
    if progress:
        progress(f"3DS: writing GLB {out_path.name} ({len(motions)} animation(s))…")
    return write_model_glb(model, textures, out_path, animations=motions)


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
