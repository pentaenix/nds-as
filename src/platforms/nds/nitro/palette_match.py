"""Texture/palette pairing heuristics for TEX0 decode."""
from __future__ import annotations

from .types import PaletteEntry, Tex0Info, TextureEntry

def _palette_name_candidates(texture_name: str) -> set[str]:
    tl = texture_name.casefold()
    out = {tl, f"{tl}_pl", f"{tl}_pal", f"{tl}pl", f"{tl}p"}
    for suffix in ("_lm0", "_lm1", "_lm2", "_lm3", "_lm"):
        if tl.endswith(suffix):
            stem = tl[: -len(suffix)]
            out |= {
                stem,
                f"{stem}_pl",
                f"{stem}_pal",
                f"{stem}pl",
                f"{tl}_pl",
                f"{tl}_pal",
                f"{tl}pl",
            }
    return out


def palette_options_for_texture(
    texture: TextureEntry,
    palettes: list[PaletteEntry],
    *,
    strict: bool = True,
    palette_hint: str | None = None,
    texture_index: int | None = None,
    paired_count: int | None = None,
) -> list[PaletteEntry | None]:
    if texture.format_id == 7:
        return [None]
    if not palettes:
        return []

    ordered: list[PaletteEntry] = []
    seen_ids: set[int] = set()

    def add_palette(palette: PaletteEntry) -> None:
        key = id(palette)
        if key not in seen_ids:
            seen_ids.add(key)
            ordered.append(palette)

    if palette_hint:
        hint = palette_hint.casefold()
        for palette in palettes:
            if palette.name.casefold() == hint:
                add_palette(palette)
        if ordered:
            return ordered

    if texture_index is not None and 0 <= texture_index < len(palettes):
        if paired_count is None or paired_count == len(palettes):
            add_palette(palettes[texture_index])

    exact_names = _palette_name_candidates(texture.name)
    for palette in palettes:
        if palette.name.casefold() in exact_names:
            add_palette(palette)
        if palette.name.casefold() == texture.name.casefold():
            add_palette(palette)

    if ordered:
        return ordered
    if strict:
        return []
    return list(palettes)


def choose_palette(texture: TextureEntry, palettes: list[PaletteEntry], *, palette_hint: str | None = None) -> PaletteEntry | None:
    opts = palette_options_for_texture(texture, palettes, strict=True, palette_hint=palette_hint)
    return opts[0] if opts else None

def palette_candidates_for_4x4(tex0: Tex0Info, palette: PaletteEntry | None, pal_index_word: int) -> list[int]:
  # apicula uses (pal_index_word & 0x3FFF) << 1 as a u16 index from the palette
    # entry base. Older DSM builds multiplied by 4, which breaks Gen 5 lightmaps.
    pal_u16_index = (pal_index_word & 0x3FFF) << 1
    byte_base = palette.offset if palette is not None else 0
    bases = [
        byte_base + pal_u16_index * 2,
        pal_u16_index * 2,
        byte_base,
    ]
    legacy_off = (pal_index_word & 0x3FFF) * 4
    if palette is not None:
        bases.extend([palette.offset + legacy_off, palette.offset + pal_u16_index * 2])
    bases.append(legacy_off)
    out: list[int] = []
    seen: set[int] = set()
    for off in bases:
        if 0 <= off < len(tex0.block4) and off not in seen:
            seen.add(off)
            out.append(off)
    return out

