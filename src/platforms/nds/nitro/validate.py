"""TEX0 structural validation and candidate scoring."""
from __future__ import annotations

from .palette_match import palette_options_for_texture
from .types import DecodedImage, PaletteEntry, Tex0Info, TextureDecodeFailure, TextureEntry

def texture_data_requirements(texture: TextureEntry) -> dict:
    w = texture.width
    h = texture.height
    fmt = texture.format_id
    if fmt == 1:
        return {"block1": w * h, "palette_colors": 32}
    if fmt == 2:
        return {"block1": (w * h + 3) // 4, "palette_colors": 4}
    if fmt == 3:
        return {"block1": (w * h + 1) // 2, "palette_colors": 16}
    if fmt == 4:
        return {"block1": w * h, "palette_colors": 256}
    if fmt == 5:
        blocks_x = (w + 3) // 4
        blocks_y = (h + 3) // 4
        block_count = blocks_x * blocks_y
        return {
            "block2": block_count * 4,
            "block3": block_count * 2,
            "palette_colors": 4,
        }
    if fmt == 6:
        return {"block1": w * h, "palette_colors": 8}
    if fmt == 7:
        return {"block1": w * h * 2, "palette_colors": 0}
    return {"unsupported": True}


def _used_palette_colors(texture: TextureEntry, tex0: Tex0Info, fallback: int) -> int:
    """Return the palette span actually addressed by the texture's texels.

    Game Freak commonly stores deliberately short palettes (for example an
    eight-color palette on a 4bpp image). Requiring the format's theoretical
    maximum rejects valid water/foam frames and the last palette in some map
    archives.
    """
    start = texture.offset
    pixels = texture.width * texture.height
    if texture.format_id in {1, 4, 6}:
        raw = tex0.block1[start : start + pixels]
        if not raw:
            return fallback
        mask = {1: 0x1F, 4: 0xFF, 6: 0x07}[texture.format_id]
        return max((value & mask) for value in raw) + 1
    if texture.format_id == 2:
        raw = tex0.block1[start : start + (pixels + 3) // 4]
        return max((byte >> shift) & 0x03 for byte in raw for shift in (0, 2, 4, 6)) + 1 if raw else fallback
    if texture.format_id == 3:
        raw = tex0.block1[start : start + (pixels + 1) // 2]
        return max(nibble for byte in raw for nibble in (byte & 0x0F, byte >> 4)) + 1 if raw else fallback
    return fallback

def validate_texture_ranges(texture: TextureEntry, palette: PaletteEntry | None, tex0: Tex0Info) -> list[str]:
    problems: list[str] = []
    w, h = texture.width, texture.height
    fmt = texture.format_id
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        problems.append(f"invalid dimensions: {w}x{h}")
        return problems
    if fmt < 1 or fmt > 7:
        problems.append(f"unsupported format id {fmt}")
        return problems

    req = texture_data_requirements(texture)
    if req.get("unsupported"):
        problems.append(f"unsupported format id {fmt}")
        return problems

    tex_off = texture.offset
    if fmt in {1, 2, 3, 4, 6}:
        need = req["block1"]
        end = tex_off + need
        if end > len(tex0.block1):
            problems.append(f"block1 out of range: need end 0x{end:X}, block1 len 0x{len(tex0.block1):X}")
    elif fmt == 5:
        need2 = req["block2"]
        need3 = req["block3"]
        end2 = tex_off + need2
        end3 = (tex_off // 2) + need3
        if end2 > len(tex0.block2):
            problems.append(f"block2 out of range: need end 0x{end2:X}, block2 len 0x{len(tex0.block2):X}")
        if end3 > len(tex0.block3):
            problems.append(f"block3 out of range: need end 0x{end3:X}, block3 len 0x{len(tex0.block3):X}")
    elif fmt == 7:
        need = req["block1"]
        end = tex_off + need
        if end > len(tex0.block1):
            problems.append(f"block1 out of range: need end 0x{end:X}, block1 len 0x{len(tex0.block1):X}")

    pal_colors = req.get("palette_colors", 0)
    if pal_colors:
        if palette is None:
            problems.append(f"missing palette for indexed format {fmt}")
        else:
            used_colors = _used_palette_colors(texture, tex0, pal_colors)
            pal_end = palette.offset + used_colors * 2
            if pal_end > len(tex0.block4):
                problems.append(f"palette out of range: need end 0x{pal_end:X}, block4 len 0x{len(tex0.block4):X}")
    return problems

def _count_valid_texture_pairs(tex0: Tex0Info) -> int:
    count = 0
    paired_count = len(tex0.textures) if len(tex0.textures) == len(tex0.palettes) else None
    for index, texture in enumerate(tex0.textures):
        palettes = palette_options_for_texture(
            texture,
            tex0.palettes,
            strict=True,
            texture_index=index,
            paired_count=paired_count,
        )
        if texture.format_id == 7:
            palettes = [None]
        for palette in palettes:
            if not validate_texture_ranges(texture, palette, tex0):
                count += 1
    return count


def score_tex0_candidate_structural(tex0: Tex0Info) -> tuple[int, int, int, int]:
    """Rank TEX0 layout candidates without decoding pixels (fast index / manifest path)."""
    valid_pairs = _count_valid_texture_pairs(tex0)
    named_textures = sum(1 for texture in tex0.textures if texture.name.strip())
    richness = (
        len(tex0.textures)
        + len(tex0.palettes)
        + len(tex0.block1)
        + len(tex0.block2)
        + len(tex0.block3)
        + len(tex0.block4)
    )
    return (valid_pairs, named_textures, len(tex0.textures), richness)


def score_tex0_candidate(
    tex0: Tex0Info,
    images: list[DecodedImage],
    *,
    requested_names: set[str] | None = None,
) -> tuple[int, int, int, int, int]:
    requested_cf = {name.casefold() for name in (requested_names or set())}
    decoded_requested = 0
    if requested_cf:
        for image in images:
            base_name = image.name.split("__", 1)[0]
            if base_name.casefold() in requested_cf or image.name.casefold() in requested_cf:
                decoded_requested += 1
    palette_hint_matches = sum(1 for image in images if image.palette_name)
    total = len(images)
    valid_pairs = _count_valid_texture_pairs(tex0)
    richness = len(tex0.textures) + len(tex0.palettes) + len(tex0.block1) + len(tex0.block2) + len(tex0.block3) + len(tex0.block4)
    return (decoded_requested, palette_hint_matches, total, valid_pairs, richness)


def format_decode_failure(failure: TextureDecodeFailure, *, source_path: str = "") -> list[str]:
    where = f"exact name {failure.texture_name} found in {source_path}" if source_path else f"exact name {failure.texture_name}"
    lines = [
        where,
        f"  fmt={failure.format_id} size={failure.width}x{failure.height} tex_offset=0x{failure.texture_offset:X}"
        + (f" palette={failure.palette_hint}" if failure.palette_hint else "")
        + (f" layout={failure.layout_name}" if failure.layout_name else ""),
    ]
    for reason in failure.reasons:
        lines.append(f"  decode failed: {reason}")
    return lines
