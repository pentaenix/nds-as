from __future__ import annotations

from dataclasses import dataclass, field

from .nitro_names import extract_nitro_names
from .nitro_textures import Tex0Info, parse_namelist, parse_tex0_manifest
from .util import read_u16le, read_u32le


@dataclass(slots=True)
class MaterialBinding:
    material_name: str
    texture_name: str | None = None
    palette_name: str | None = None
    texcoord_transform_mode: int | None = None
    scale_s: float | None = None
    scale_t: float | None = None
    rotate: float | None = None
    translate_s: float | None = None
    translate_t: float | None = None


@dataclass(slots=True)
class NsbmdManifest:
    model_names: list[str] = field(default_factory=list)
    materials: list[MaterialBinding] = field(default_factory=list)
    embedded_tex0: Tex0Info | None = None
    raw_names: list[str] = field(default_factory=list)
    parse_notes: list[str] = field(default_factory=list)


def _container_subfile_offsets(data: bytes, expected_magic: bytes) -> list[int]:
    if len(data) < 0x14 or data[:4] != expected_magic:
        return []
    count = read_u16le(data, 0x0E)
    out: list[int] = []
    for i in range(min(count, 16)):
        pos = 0x10 + i * 4
        if pos + 4 > len(data):
            break
        off = read_u32le(data, pos)
        if 0 <= off <= len(data) - 4:
            out.append(off)
    return out


def find_mdl0_offset(data: bytes) -> int | None:
    for off in _container_subfile_offsets(data, b"BMD0"):
        if data[off:off + 4] == b"MDL0":
            return off
    pos = data.find(b"MDL0")
    return pos if pos >= 0 else None


def find_embedded_tex0(data: bytes) -> Tex0Info | None:
    return parse_tex0_manifest(data)


def _valid_absolute(data: bytes, off: int, size: int = 4) -> bool:
    return 0 <= off <= len(data) - size


def _read_idx_list(data: bytes, entry_abs: int, payload: bytes, list_base: int, material_count: int) -> list[int]:
    """Read a MaterialIdxList from a pairing entry.

    Docs say the offset is relative to the MaterialIdxList itself. Some old docs
    disagree, so we try a few conservative bases and choose the first list that
    produces valid material indices.
    """
    if len(payload) < 4 or material_count <= 0:
        return []
    off = read_u16le(payload, 0)
    count = payload[2]
    if count <= 0 or count > 255:
        return []
    bases = [entry_abs, list_base, entry_abs - 4]
    for base in bases:
        start = base + off
        if not _valid_absolute(data, start, count):
            continue
        values = list(data[start:start + count])
        if values and all(0 <= v < material_count for v in values):
            return values
    return []


def _parse_pairings(data: bytes, pairing_abs: int, material_count: int) -> dict[int, str]:
    out: dict[int, str] = {}
    if not _valid_absolute(data, pairing_abs, 16):
        return out
    entries = parse_namelist(data, pairing_abs, 4)
    for entry in entries:
        idxs = _read_idx_list(data, entry.entry_offset, entry.data, pairing_abs, material_count)
        for idx in idxs:
            out[idx] = entry.name
    return out


def _parse_material_list(data: bytes, material_list_abs: int) -> list[MaterialBinding]:
    if not _valid_absolute(data, material_list_abs, 8):
        return []
    tex_pair_off = read_u16le(data, material_list_abs)
    pal_pair_off = read_u16le(data, material_list_abs + 2)
    material_entries = parse_namelist(data, material_list_abs + 4, 4)
    material_names = [e.name for e in material_entries]
    if not material_names:
        return []
    tex_pairs = _parse_pairings(data, material_list_abs + tex_pair_off, len(material_names)) if tex_pair_off else {}
    pal_pairs = _parse_pairings(data, material_list_abs + pal_pair_off, len(material_names)) if pal_pair_off else {}

    bindings: list[MaterialBinding] = []
    for idx, name in enumerate(material_names):
        tex_name = tex_pairs.get(idx)
        pal_name = pal_pairs.get(idx)
        mode = None
        # Material NameList entries are u32 offsets relative to the MaterialList.
        # If this material data is reachable, read texcoord transform mode from
        # material TEXIMAGE_PARAMS bits 30..31 for later preview/export work.
        try:
            if len(material_entries[idx].data) >= 4:
                mat_rel = read_u32le(material_entries[idx].data, 0)
                mat_abs = material_list_abs + mat_rel
                if _valid_absolute(data, mat_abs + 0x14, 4):
                    tex_params = read_u32le(data, mat_abs + 0x14)
                    mode = (tex_params >> 30) & 0x3
        except Exception:
            mode = None
        bindings.append(MaterialBinding(material_name=name, texture_name=tex_name, palette_name=pal_name, texcoord_transform_mode=mode))
    return bindings


def _parse_model_at(data: bytes, model_abs: int) -> tuple[list[MaterialBinding], str | None]:
    if not _valid_absolute(data, model_abs, 0x14):
        return [], "model offset outside file"
    try:
        materials_off = read_u32le(data, model_abs + 0x08)
        num_materials = data[model_abs + 0x11] if model_abs + 0x12 <= len(data) else 0
        material_list_abs = model_abs + materials_off
        mats = _parse_material_list(data, material_list_abs)
        if mats:
            return mats, (
                f"parsed material list at 0x{material_list_abs:X} "
                f"({len(mats)} structural material binding(s); raw model byte +0x11 = {num_materials})"
            )
        return [], f"material list present at 0x{material_list_abs:X}, but no structural material bindings parsed"
    except Exception as exc:
        return [], f"material parser failed: {exc}"


def parse_material_bindings(data: bytes) -> tuple[list[str], list[MaterialBinding], list[str]]:
    notes: list[str] = []
    model_names: list[str] = []
    material_bindings: list[MaterialBinding] = []
    mdl_off = find_mdl0_offset(data)
    if mdl_off is None:
        notes.append("no MDL0 model block found")
        return model_names, material_bindings, notes
    notes.append(f"MDL0 model block found at 0x{mdl_off:X}")
    if not _valid_absolute(data, mdl_off + 8, 16):
        notes.append("MDL0 block too small for model dictionary")
        return model_names, material_bindings, notes
    model_entries = parse_namelist(data, mdl_off + 8, 4)
    if not model_entries:
        notes.append("model NameList could not be parsed")
        return model_names, material_bindings, notes
    model_names = [e.name for e in model_entries]
    notes.append(f"parsed {len(model_names)} model dictionary entry(s)")
    for model_entry in model_entries:
        if len(model_entry.data) < 4:
            continue
        rel = read_u32le(model_entry.data, 0)
        model_abs = mdl_off + rel
        mats, note = _parse_model_at(data, model_abs)
        if note:
            notes.append(f"{model_entry.name}: {note}")
        material_bindings.extend(mats)
    # De-duplicate identical bindings across model entries.
    dedup: list[MaterialBinding] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for m in material_bindings:
        key = (m.material_name, m.texture_name, m.palette_name)
        if key not in seen:
            seen.add(key)
            dedup.append(m)
    return model_names, dedup, notes


def _fallback_materials_from_embedded_tex0(tex0: Tex0Info) -> list[MaterialBinding]:
    # If the MDL0 material pairing table could not be parsed but a TEX0 exists,
    # expose texture/palette pairs from the texture dictionary as material-like
    # requests. This is deterministic (it comes from TEX0 dictionaries), unlike
    # raw string scanning.
    bindings: list[MaterialBinding] = []
    palette_by_name = {p.name.casefold(): p.name for p in tex0.palettes}
    for tex in tex0.textures:
        candidates = [tex.name, f"{tex.name}_pl", f"{tex.name}_pal", f"{tex.name}pl", f"{tex.name}p"]
        pal_name = next((palette_by_name[c.casefold()] for c in candidates if c.casefold() in palette_by_name), None)
        bindings.append(MaterialBinding(material_name=tex.name, texture_name=tex.name, palette_name=pal_name))
    return bindings


def parse_nsbmd_manifest(data: bytes) -> NsbmdManifest | None:
    if len(data) < 4 or data[:4] != b"BMD0":
        return None
    embedded = find_embedded_tex0(data)
    notes: list[str] = []
    if embedded is not None:
        notes.append(f"embedded TEX0 texture block found ({len(embedded.textures)} texture(s), {len(embedded.palettes)} palette(s))")
    else:
        notes.append("no embedded TEX0 texture block found")

    model_names, materials, material_notes = parse_material_bindings(data)
    notes.extend(material_notes)
    if materials:
        notes.append(f"structural material bindings parsed: {len(materials)}")
    elif embedded is not None and embedded.textures:
        materials = _fallback_materials_from_embedded_tex0(embedded)
        notes.append(f"using embedded TEX0 dictionary as fallback material requests: {len(materials)}")
    else:
        # Last-resort compatibility for synthetic/minimal tests and odd files.
        # The resolver will only accept these if they exactly match a real TEX0
        # texture dictionary and can decode an image.
        names = sorted(extract_nitro_names(data))
        materials = [MaterialBinding(material_name=name, texture_name=name, palette_name=None) for name in names]
        notes.append(f"structural material bindings unavailable; debug fallback names: {len(materials)}")

    raw_names = sorted({m.material_name for m in materials} | {m.texture_name for m in materials if m.texture_name} | {m.palette_name for m in materials if m.palette_name})
    return NsbmdManifest(model_names=model_names, materials=materials, embedded_tex0=embedded, raw_names=raw_names, parse_notes=notes)
