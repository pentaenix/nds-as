from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .util import read_u16le, read_u32le, sanitize_component

try:
    from .compression import decompress_lz10, looks_like_lz10
except Exception:  # pragma: no cover
    def looks_like_lz10(_data: bytes) -> bool:
        return False

    def decompress_lz10(data: bytes) -> bytes:
        raise ValueError("LZ10 unavailable")

try:  # Pillow is used for PNG writing/contact sheets; DSM still imports without it.
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None


@dataclass(slots=True)
class DecodedImage:
    name: str
    width: int
    height: int
    rgba: bytes
    source: str
    format_id: int | None = None
    palette_name: str | None = None

    def to_pil(self):
        if Image is None:
            raise RuntimeError("Pillow is required to save/view decoded images. Install requirements.txt.")
        return Image.frombytes("RGBA", (self.width, self.height), self.rgba)

    def save_png(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.to_pil().save(out)
        return out


@dataclass(slots=True)
class NitroNameEntry:
    name: str
    data: bytes
    entry_offset: int = 0
    list_offset: int = 0


@dataclass(slots=True)
class TextureEntry:
    name: str
    teximage_params: int
    unknown: int

    @property
    def offset(self) -> int:
        return (self.teximage_params & 0xFFFF) << 3

    @property
    def width(self) -> int:
        return 8 << ((self.teximage_params >> 20) & 0x7)

    @property
    def height(self) -> int:
        return 8 << ((self.teximage_params >> 23) & 0x7)

    @property
    def format_id(self) -> int:
        return (self.teximage_params >> 26) & 0x7

    @property
    def color0_transparent(self) -> bool:
        return bool((self.teximage_params >> 29) & 0x1)


@dataclass(slots=True)
class PaletteEntry:
    name: str
    offset: int
    unknown: int


@dataclass(slots=True)
class Tex0Info:
    block1: bytes
    block2: bytes
    block3: bytes
    block4: bytes
    textures: list[TextureEntry]
    palettes: list[PaletteEntry]
    layout_name: str = ""


@dataclass(slots=True)
class TextureDecodeFailure:
    texture_name: str
    palette_hint: str | None
    format_id: int
    width: int
    height: int
    texture_offset: int
    layout_name: str
    reasons: list[str]


@dataclass(slots=True)
class GuidedDecodeReport:
    images: list[DecodedImage]
    failures: list[TextureDecodeFailure]
    candidate_summaries: list[str]
    selected_layout: str | None = None


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
            pal_end = palette.offset + pal_colors * 2
            if pal_end > len(tex0.block4):
                problems.append(f"palette out of range: need end 0x{pal_end:X}, block4 len 0x{len(tex0.block4):X}")
    return problems


def attempt_decode_texture(texture: TextureEntry, palette: PaletteEntry | None, tex0: Tex0Info) -> tuple[DecodedImage | None, list[str]]:
    problems = validate_texture_ranges(texture, palette, tex0)
    if problems:
        return None, problems
    try:
        decoded = decode_texture(texture, palette, tex0)
    except Exception as exc:
        return None, [f"decode exception: {exc}"]
    if decoded is None:
        if texture.format_id == 5:
            return None, ["4x4 decode produced no visible blocks (palette index or texel data unreadable)"]
        return None, ["decode returned no image"]
    return decoded, []


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


def _format_decode_failure(failure: TextureDecodeFailure, *, source_path: str = "") -> list[str]:
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


def _decode_tex0_prepared(tex0: Tex0Info, *, max_images: int = 128, mode: str = "resolved") -> list[DecodedImage]:
    images: list[DecodedImage] = []
    strict = mode not in {"all-palettes", "debug", "all"}
    paired_count = len(tex0.textures) if len(tex0.textures) == len(tex0.palettes) else None
    for index, texture in enumerate(tex0.textures):
        if len(images) >= max_images:
            break
        palettes = palette_options_for_texture(
            texture,
            tex0.palettes,
            strict=strict,
            texture_index=index,
            paired_count=paired_count,
        )
        if texture.format_id == 7:
            palettes = [None]
        for palette in palettes:
            if len(images) >= max_images:
                break
            decoded, _problems = attempt_decode_texture(texture, palette, tex0)
            if decoded is not None:
                if palette is not None and decoded.palette_name:
                    decoded.name = f"{decoded.name}__{decoded.palette_name}" if mode in {"all-palettes", "debug", "all"} else decoded.name
                images.append(decoded)
    return images


def decode_btx_images(data: bytes, *, max_images: int = 128, mode: str = "resolved") -> list[DecodedImage]:
    """Decode readable images from a BTX0/NSBTX or BMD0-with-embedded-TEX blob.

    ``mode="resolved"`` decodes only structurally matched texture/palette
    pairs. ``mode="all-palettes"`` is a viewer/debug mode that renders each
    texture against every compatible palette so users can still inspect archives
    whose pairing table has not been decoded yet. DSM's model resolver uses the
    strict mode; texture-contact-sheet viewing uses all-palettes.
    """
    candidates = parse_tex0_candidates(data)
    if not candidates:
        return []

    best_images: list[DecodedImage] = []
    best_score: tuple[int, ...] = (-1, -1, -1, -1, -1)
    for candidate in candidates:
        prepared = prepare_tex0(candidate)
        images = _decode_tex0_prepared(prepared, max_images=max_images, mode=mode)
        score = score_tex0_candidate(prepared, images)
        if score > best_score:
            best_score = score
            best_images = images
    return best_images[:max_images]


def decode_guided_tex0_images(
    data: bytes,
    *,
    texture_requests: Iterable[tuple[str, str | None]],
    max_images: int = 128,
) -> list[DecodedImage]:
    """Decode embedded/external TEX0 images using explicit material texture requests."""
    return decode_guided_tex0_report(data, texture_requests=texture_requests, max_images=max_images).images


def decode_guided_tex0_report(
    data: bytes,
    *,
    texture_requests: Iterable[tuple[str, str | None]],
    max_images: int = 128,
) -> GuidedDecodeReport:
    """Decode guided TEX0 images and return diagnostics for every candidate layout."""
    requests = list(texture_requests)
    requested_names = {name for name, _hint in requests if name}
    candidates = parse_tex0_candidates(data)
    if not candidates:
        return GuidedDecodeReport([], [], [])

    best_images: list[DecodedImage] = []
    best_failures: list[TextureDecodeFailure] = []
    best_score: tuple[int, ...] = (-1, -1, -1, -1, -1)
    best_layout: str | None = None
    candidate_summaries: list[str] = []
    selected_index: int | None = None

    for cand_index, candidate in enumerate(candidates):
        tex0 = prepare_tex0(candidate)
        if not tex0.textures:
            candidate_summaries.append(f"candidate {cand_index} ({tex0.layout_name or 'unknown'}): no textures parsed")
            continue
        tex_by_name = {t.name.casefold(): t for t in tex0.textures}
        paired_count = len(tex0.textures) if len(tex0.textures) == len(tex0.palettes) else None
        images: list[DecodedImage] = []
        failures: list[TextureDecodeFailure] = []
        seen: set[tuple[str, str | None]] = set()
        common_failure = ""

        for texture_name, palette_hint in requests:
            if len(images) >= max_images:
                break
            texture = tex_by_name.get(texture_name.casefold())
            if texture is None:
                continue
            texture_index = next((i for i, t in enumerate(tex0.textures) if t.name == texture.name), None)
            decoded_any = False
            last_reasons: list[str] = []
            for strict in (True, False):
                if len(images) >= max_images or decoded_any:
                    break
                palettes = palette_options_for_texture(
                    texture,
                    tex0.palettes,
                    strict=strict,
                    palette_hint=palette_hint,
                    texture_index=texture_index,
                    paired_count=paired_count,
                )
                if texture.format_id == 7:
                    palettes = [None]
                if not palettes and texture.format_id != 7:
                    last_reasons = [f"missing palette for indexed format {texture.format_id}"]
                    continue
                for palette in palettes:
                    if len(images) >= max_images:
                        break
                    key = (texture.name, palette.name if palette else None)
                    if key in seen:
                        continue
                    decoded, problems = attempt_decode_texture(texture, palette, tex0)
                    if decoded is not None:
                        seen.add(key)
                        images.append(decoded)
                        decoded_any = True
                        break
                    last_reasons = problems or last_reasons
                if decoded_any:
                    break
            if not decoded_any:
                failures.append(TextureDecodeFailure(
                    texture_name=texture.name,
                    palette_hint=palette_hint,
                    format_id=texture.format_id,
                    width=texture.width,
                    height=texture.height,
                    texture_offset=texture.offset,
                    layout_name=tex0.layout_name,
                    reasons=last_reasons or ["no palette/image could be decoded"],
                ))
                if last_reasons and not common_failure:
                    common_failure = last_reasons[0]

        score = score_tex0_candidate(tex0, images, requested_names=requested_names)
        decoded_requested = score[0]
        candidate_summaries.append(
            f"candidate {cand_index} ({tex0.layout_name or 'unknown'}): decoded {decoded_requested} requested, {len(images)} total"
            + (f", common failure {common_failure}" if common_failure and not images else "")
        )
        if score > best_score:
            best_score = score
            best_images = images
            best_failures = failures
            best_layout = tex0.layout_name or None
            selected_index = cand_index

    if selected_index is not None and 0 <= selected_index < len(candidate_summaries):
        candidate_summaries[selected_index] += "; selected"
    elif not best_images and candidates:
        richest = max(
            enumerate(candidates),
            key=lambda item: (
                len(item[1].textures),
                len(item[1].palettes),
                len(item[1].block1) + len(item[1].block2) + len(item[1].block3) + len(item[1].block4),
            ),
        )[0]
        tex0 = prepare_tex0(candidates[richest])
        _, failures = _guided_failures_for_candidate(tex0, requests)
        if failures:
            best_failures = failures

    return GuidedDecodeReport(best_images[:max_images], best_failures, candidate_summaries, best_layout)


def _guided_failures_for_candidate(
    tex0: Tex0Info,
    requests: list[tuple[str, str | None]],
) -> tuple[list[DecodedImage], list[TextureDecodeFailure]]:
    tex_by_name = {t.name.casefold(): t for t in tex0.textures}
    paired_count = len(tex0.textures) if len(tex0.textures) == len(tex0.palettes) else None
    images: list[DecodedImage] = []
    failures: list[TextureDecodeFailure] = []
    seen: set[tuple[str, str | None]] = set()
    for texture_name, palette_hint in requests:
        texture = tex_by_name.get(texture_name.casefold())
        if texture is None:
            continue
        texture_index = next((i for i, t in enumerate(tex0.textures) if t.name == texture.name), None)
        decoded_any = False
        last_reasons: list[str] = []
        for strict in (True, False):
            palettes = palette_options_for_texture(
                texture,
                tex0.palettes,
                strict=strict,
                palette_hint=palette_hint,
                texture_index=texture_index,
                paired_count=paired_count,
            )
            if texture.format_id == 7:
                palettes = [None]
            for palette in palettes:
                key = (texture.name, palette.name if palette else None)
                if key in seen:
                    continue
                decoded, problems = attempt_decode_texture(texture, palette, tex0)
                if decoded is not None:
                    seen.add(key)
                    images.append(decoded)
                    decoded_any = True
                    break
                last_reasons = problems or last_reasons
            if decoded_any:
                break
        if not decoded_any:
            failures.append(TextureDecodeFailure(
                texture_name=texture.name,
                palette_hint=palette_hint,
                format_id=texture.format_id,
                width=texture.width,
                height=texture.height,
                texture_offset=texture.offset,
                layout_name=tex0.layout_name,
                reasons=last_reasons or ["no palette/image could be decoded"],
            ))
    return images, failures


def parse_tex0_candidates(data: bytes) -> list[Tex0Info]:
    """Return every plausible TEX0 layout candidate found in a blob."""
    tex_off = find_tex0_offset(data)
    if tex_off is None or tex_off + 0x38 > len(data):
        return []
    if data[tex_off:tex_off + 4] != b"TEX0":
        return []

    candidates: list[Tex0Info] = []
    layouts = [
        ("apicula", 0x1C, 0x24, 0x28, 0x30, True, 0x34, 0x38),
        ("scurest", 0x18, 0x20, 0x24, 0x2C, False, 0x30, 0x34),
        ("vgresource", 0x1C, 0x24, 0x28, 0x30, True, 0x34, 0x38),
    ]
    for layout_name, block2_len_pos, block2_off_pos, block3_off_pos, block4_len_pos, block4_len_u32, palettes_off_pos, block4_off_pos in layouts:
        info = _parse_tex0_layout(
            data,
            tex_off,
            block2_len_pos,
            block2_off_pos,
            block3_off_pos,
            block4_len_pos,
            block4_len_u32,
            palettes_off_pos,
            block4_off_pos,
            layout_name=layout_name,
        )
        if info is not None:
            candidates.append(info)
    return candidates


def _tex0_data_end(data: bytes, tex_off: int) -> int:
    end = len(data)
    if tex_off > 0 and len(data) >= 0x0C and data[:4] in {b"BMD0", b"BTX0"}:
        container_size = read_u32le(data, 0x08)
        if container_size > tex_off:
            end = min(end, container_size)
    return end


def _rel_slice_between(data: bytes, tex_off: int, start_rel: int, end_rel: int, fallback_len: int) -> bytes:
    if start_rel > 0 and end_rel > start_rel:
        start = tex_off + start_rel
        end = tex_off + end_rel
        if 0 <= start < len(data) and end <= len(data) and end > start:
            return data[start:end]
    if start_rel > 0:
        start = tex_off + start_rel
        end = min(start + max(0, fallback_len), len(data))
        if start < len(data) and end > start:
            return data[start:end]
    return b""


def _parse_tex0_layout(
    data: bytes,
    tex_off: int,
    block2_len_pos: int,
    block2_off_pos: int,
    block3_off_pos: int,
    block4_len_pos: int,
    block4_len_u32: bool,
    palettes_off_pos: int,
    block4_off_pos: int,
    *,
    layout_name: str = "",
) -> Tex0Info | None:
    try:
        block1_len = read_u16le(data, tex_off + 0x0C) << 3
        textures_off = read_u16le(data, tex_off + 0x0E)
        block1_off = read_u32le(data, tex_off + 0x14)
        block2_len = read_u16le(data, tex_off + block2_len_pos) << 3
        block2_off = read_u32le(data, tex_off + block2_off_pos)
        block3_off = read_u32le(data, tex_off + block3_off_pos)
        if block4_len_u32:
            raw_len = read_u32le(data, tex_off + block4_len_pos)
            block4_len = raw_len << 3 if raw_len < (1 << 24) else 0
        else:
            block4_len = read_u16le(data, tex_off + block4_len_pos) << 3
        palettes_off = read_u32le(data, tex_off + palettes_off_pos)
        block4_off = read_u32le(data, tex_off + block4_off_pos)
    except Exception:
        return None

    tex0_end_rel = _tex0_data_end(data, tex_off) - tex_off

    def slice_block(start_rel: int, next_rel: int, fallback_len: int) -> bytes:
        if start_rel > 0 and next_rel > start_rel:
            return _rel_slice_between(data, tex_off, start_rel, next_rel, 0)
        if start_rel > 0 and tex0_end_rel > start_rel and next_rel <= 0:
            return _rel_slice_between(data, tex_off, start_rel, tex0_end_rel, 0)
        return _rel_slice_between(data, tex_off, start_rel, 0, fallback_len)

    if textures_off <= 0 or tex_off + textures_off >= len(data):
        return None
    if palettes_off <= 0 or tex_off + palettes_off >= len(data):
        return None

    block1 = slice_block(block1_off, block2_off, block1_len)
    block2 = slice_block(block2_off, block3_off, block2_len)
    block3 = slice_block(block3_off, block4_off, max(block2_len // 2, 0))
    block4 = slice_block(block4_off, tex0_end_rel, block4_len)

    textures: list[TextureEntry] = []
    for entry in parse_namelist(data, tex_off + textures_off, 8):
        if len(entry.data) >= 8:
            params = read_u32le(entry.data, 0)
            tex = TextureEntry(entry.name, params, read_u32le(entry.data, 4))
            if 0 < tex.width <= 4096 and 0 < tex.height <= 4096 and 1 <= tex.format_id <= 7:
                textures.append(tex)

    palettes: list[PaletteEntry] = []
    for entry in parse_namelist(data, tex_off + palettes_off, 4):
        if len(entry.data) >= 4:
            pal = PaletteEntry(entry.name, read_u16le(entry.data, 0) << 3, read_u16le(entry.data, 2))
            if 0 <= pal.offset < max(len(block4), 1 << 30):
                palettes.append(pal)

    if not textures:
        return None
    return Tex0Info(
        block1=block1,
        block2=block2,
        block3=block3,
        block4=block4,
        textures=textures,
        palettes=palettes,
        layout_name=layout_name,
    )


def parse_tex0(data: bytes) -> Tex0Info | None:
    """Parse a TEX0 block from BTX0 or embedded NSBMD texture data.

    Nintendo DS documentation in the wild describes two closely related TEX0
    header layouts. Older DSM builds hard-coded one of them, which meant some
    real Pokémon BMD0 embedded TEX0 blocks exposed texture names but failed to
    reach the palette/image data. This parser tries both layouts and returns the
    richest structurally valid result.
    """
    candidates = parse_tex0_candidates(data)
    if not candidates:
        return None

    def richness(info: Tex0Info) -> tuple[int, ...]:
        prepared = prepare_tex0(info)
        images = _decode_tex0_prepared(prepared, max_images=32, mode="all-palettes")
        return score_tex0_candidate(prepared, images)

    return max(candidates, key=richness)

def parse_tex0_manifest(data: bytes) -> Tex0Info | None:
    return parse_tex0(data)


def find_tex0_offset(data: bytes) -> int | None:
    if len(data) < 4:
        return None
    if data[:4] == b"TEX0":
        return 0
    if data[:4] in {b"BTX0", b"BMD0"} and len(data) >= 0x14:
        header_size = read_u16le(data, 0x0C)
        subfiles = read_u16le(data, 0x0E)
        table_start = 0x10
        for i in range(min(subfiles, 16)):
            off_pos = table_start + i * 4
            if off_pos + 4 <= len(data):
                off = read_u32le(data, off_pos)
                if 0 <= off <= len(data) - 4 and data[off:off + 4] == b"TEX0":
                    return off
        if 0 < header_size < len(data) and data[header_size:header_size + 4] == b"TEX0":
            return header_size
    pos = data.find(b"TEX0")
    return pos if pos >= 0 else None


def parse_namelist(data: bytes, off: int, fallback_entry_size: int) -> list[NitroNameEntry]:
    """Parse a Nitro NameList(T).

    Returns each entry's payload plus absolute offsets. The offsets matter for
    MDL0 material texture/palette pairings, whose MaterialIdxList payload points
    to a u8 list relative to the payload itself.
    """
    if off <= 0 or off + 16 > len(data):
        return []
    try:
        count = data[off + 1]
        total_size = read_u16le(data, off + 2)
        if count <= 0 or count > 2048 or total_size < 16 or off + total_size > len(data):
            return []

        # Normal Nitro Header layout:
        # 0x00 BBH, 0x04 unknown header (8 bytes), then count*u32 unknowns,
        # then HH for element size/data section length.
        element_size_pos = off + 12 + count * 4
        if element_size_pos + 4 > off + total_size:
            return []
        element_size = read_u16le(data, element_size_pos) or fallback_entry_size
        if element_size <= 0 or element_size > 4096:
            element_size = fallback_entry_size
        data_section_size = read_u16le(data, element_size_pos + 2)
        data_start = element_size_pos + 4
        if data_section_size <= 0 or data_section_size > total_size:
            data_section_size = count * element_size
        names_start = data_start + data_section_size

        # Some files report a larger data section than count*element_size. Names
        # are still the last 16*count bytes of the NameList, so recover.
        if names_start + 16 * count > off + total_size:
            names_start = off + total_size - 16 * count
        if names_start < data_start or names_start + 16 * count > len(data):
            return []

        entries: list[NitroNameEntry] = []
        for i in range(count):
            entry_start = data_start + i * element_size
            if entry_start + min(element_size, fallback_entry_size) > len(data):
                break
            raw_name = data[names_start + i * 16:names_start + (i + 1) * 16]
            name = decode_nitro_name(raw_name) or f"entry_{i:03d}"
            entries.append(NitroNameEntry(
                name=name,
                data=data[entry_start:entry_start + element_size],
                entry_offset=entry_start,
                list_offset=off,
            ))
        return entries
    except Exception:
        return []

def decode_nitro_name(raw: bytes) -> str | None:
    raw = raw.split(b"\0", 1)[0].strip()
    if not raw:
        return None
    try:
        return raw.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return raw.decode("shift_jis", errors="replace")


def _maybe_decompress_block(data: bytes) -> bytes:
    if not data or not looks_like_lz10(data):
        return data
    try:
        return decompress_lz10(data)
    except Exception:
        return data


def prepare_tex0(tex0: Tex0Info) -> Tex0Info:
    """Return a TEX0 view with LZ10-compressed image/palette blocks expanded when possible."""
    block1 = _maybe_decompress_block(tex0.block1)
    block2 = _maybe_decompress_block(tex0.block2)
    block4 = _maybe_decompress_block(tex0.block4)
    if block1 == tex0.block1 and block2 == tex0.block2 and block4 == tex0.block4:
        return tex0
    return Tex0Info(
        block1=block1,
        block2=block2,
        block3=tex0.block3,
        block4=block4,
        textures=tex0.textures,
        palettes=tex0.palettes,
        layout_name=tex0.layout_name,
    )


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


def decode_texture(texture: TextureEntry, palette: PaletteEntry | None, tex0: Tex0Info) -> DecodedImage | None:
    fmt = texture.format_id
    w, h = texture.width, texture.height
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        return None
    if fmt == 5:
        return decode_texture_4x4(texture, palette, tex0)

    if fmt == 7:
        needed = w * h * 2
        raw = tex0.block1[texture.offset:texture.offset + needed]
        if len(raw) < needed:
            return None
        rgba = bytearray()
        for i in range(0, needed, 2):
            color = int.from_bytes(raw[i:i + 2], "little")
            r, g, b, a = bgr555_to_rgba(color, alpha_bit=True)
            rgba += bytes((r, g, b, a))
        return DecodedImage(texture.name, w, h, bytes(rgba), "BTX0 direct color", fmt, None)

    if palette is None:
        return None
    pal_count = {1: 32, 2: 4, 3: 16, 4: 256, 6: 8}.get(fmt)
    if pal_count is None:
        return None
    palette_colors = read_palette(tex0.block4, palette.offset, pal_count)
    if not palette_colors:
        return None

    rgba = bytearray()
    if fmt == 1:  # A3I5
        raw = tex0.block1[texture.offset:texture.offset + w * h]
        if len(raw) < w * h:
            return None
        for value in raw[:w * h]:
            idx = value & 0x1F
            alpha = ((value >> 5) & 0x7) * 255 // 7
            r, g, b, _ = palette_colors[idx % len(palette_colors)]
            rgba += bytes((r, g, b, alpha))
    elif fmt == 2:  # 2bpp indexed
        raw = tex0.block1[texture.offset:texture.offset + ((w * h + 3) // 4)]
        pixels = []
        for byte in raw:
            pixels.extend([(byte >> shift) & 0x3 for shift in (0, 2, 4, 6)])
        for idx in pixels[:w * h]:
            rgba += bytes(apply_color0(palette_colors[idx % len(palette_colors)], idx, texture.color0_transparent))
    elif fmt == 3:  # 4bpp indexed
        raw = tex0.block1[texture.offset:texture.offset + ((w * h + 1) // 2)]
        pixels = []
        for byte in raw:
            pixels.extend([byte & 0xF, (byte >> 4) & 0xF])
        for idx in pixels[:w * h]:
            rgba += bytes(apply_color0(palette_colors[idx % len(palette_colors)], idx, texture.color0_transparent))
    elif fmt == 4:  # 8bpp indexed
        raw = tex0.block1[texture.offset:texture.offset + w * h]
        if len(raw) < w * h:
            return None
        for idx in raw[:w * h]:
            rgba += bytes(apply_color0(palette_colors[idx % len(palette_colors)], idx, texture.color0_transparent))
    elif fmt == 6:  # A5I3
        raw = tex0.block1[texture.offset:texture.offset + w * h]
        if len(raw) < w * h:
            return None
        for value in raw[:w * h]:
            idx = value & 0x7
            alpha = ((value >> 3) & 0x1F) * 255 // 31
            r, g, b, _ = palette_colors[idx % len(palette_colors)]
            rgba += bytes((r, g, b, alpha))
    else:
        return None

    return DecodedImage(texture.name, w, h, bytes(rgba), "BTX0 indexed", fmt, palette.name)


def _mix(c0: tuple[int, int, int, int], c1: tuple[int, int, int, int], a: int, b: int, denom: int) -> tuple[int, int, int, int]:
    return (
        (c0[0] * a + c1[0] * b) // denom,
        (c0[1] * a + c1[1] * b) // denom,
        (c0[2] * a + c1[2] * b) // denom,
        (c0[3] * a + c1[3] * b) // denom,
    )


def _palette_candidates_for_4x4(tex0: Tex0Info, palette: PaletteEntry | None, pal_index_word: int) -> list[int]:
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


def decode_texture_4x4(texture: TextureEntry, palette: PaletteEntry | None, tex0: Tex0Info) -> DecodedImage | None:
    w, h = texture.width, texture.height
    if w <= 0 or h <= 0 or w > 2048 or h > 2048:
        return None
    blocks_x = (w + 3) // 4
    blocks_y = (h + 3) // 4
    block_count = blocks_x * blocks_y
    data_off = texture.offset
    texels = tex0.block2[data_off:data_off + block_count * 4]
    indices = tex0.block3[(data_off // 2):(data_off // 2) + block_count * 2]
    if len(texels) < block_count * 4 or len(indices) < block_count * 2:
        return None

    rgba = bytearray([0, 0, 0, 0] * (w * h))
    decoded_any_block = False
    for by in range(blocks_y):
        for bx in range(blocks_x):
            bi = by * blocks_x + bx
            bits = read_u32le(texels, bi * 4)
            pal_word = read_u16le(indices, bi * 2)
            mode = (pal_word >> 14) & 0x3

            colors = None
            for pal_off in _palette_candidates_for_4x4(tex0, palette, pal_word):
                base = read_palette(tex0.block4, pal_off, 4)
                if len(base) >= 2:
                    if mode == 0:
                        colors = [base[0], base[1], base[2] if len(base) > 2 else base[0], (0, 0, 0, 0)]
                    elif mode == 1:
                        colors = [base[0], base[1], _mix(base[0], base[1], 1, 1, 2), (0, 0, 0, 0)]
                    elif mode == 2:
                        colors = [base[0], base[1], base[2] if len(base) > 2 else base[0], base[3] if len(base) > 3 else base[1]]
                    else:  # mode 3
                        colors = [base[0], base[1], _mix(base[0], base[1], 5, 3, 8), _mix(base[0], base[1], 3, 5, 8)]
                    break
            if colors is None:
                continue
            decoded_any_block = True

            for py in range(4):
                for px in range(4):
                    dst_x = bx * 4 + px
                    dst_y = by * 4 + py
                    if dst_x >= w or dst_y >= h:
                        continue
                    idx = (bits >> ((py * 4 + px) * 2)) & 0x3
                    off = (dst_y * w + dst_x) * 4
                    rgba[off:off + 4] = bytes(colors[idx])
    if not decoded_any_block:
        return None
    return DecodedImage(texture.name, w, h, bytes(rgba), "BTX0 4x4 compressed", 5, palette.name if palette else None)


def read_palette(block4: bytes, offset: int, count: int) -> list[tuple[int, int, int, int]]:
    colors: list[tuple[int, int, int, int]] = []
    start = offset
    for i in range(count):
        pos = start + i * 2
        if pos + 2 > len(block4):
            break
        colors.append(bgr555_to_rgba(int.from_bytes(block4[pos:pos + 2], "little")))
    return colors


def bgr555_to_rgba(value: int, *, alpha_bit: bool = False) -> tuple[int, int, int, int]:
    r5 = value & 0x1F
    g5 = (value >> 5) & 0x1F
    b5 = (value >> 10) & 0x1F
    a = 255
    if alpha_bit and not (value & 0x8000):
        a = 0
    return ((r5 << 3) | (r5 >> 2), (g5 << 3) | (g5 >> 2), (b5 << 3) | (b5 >> 2), a)


def apply_color0(color: tuple[int, int, int, int], idx: int, color0_transparent: bool) -> tuple[int, int, int, int]:
    if idx == 0 and color0_transparent:
        return (color[0], color[1], color[2], 0)
    return color


def save_decoded_images(images: Iterable[DecodedImage], out_dir: str | Path, *, prefix: str = "texture") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    used: dict[str, int] = {}
    for image in images:
        stem = sanitize_component(image.name or prefix)
        index = used.get(stem, 0)
        used[stem] = index + 1
        if index:
            stem = f"{stem}_{index:02d}"
        path = out / f"{stem}.png"
        image.save_png(path)
        written.append(path)
    return written


def make_contact_sheet(images: list[DecodedImage], *, max_thumb: int = 128, columns: int = 4):
    if Image is None:
        raise RuntimeError("Pillow is required to build contact sheets. Install requirements.txt.")
    if not images:
        return None
    thumbs = []
    for item in images:
        im = item.to_pil()
        im.thumbnail((max_thumb, max_thumb), Image.Resampling.NEAREST)
        thumbs.append((item, im.copy()))
    label_h = 22
    cell_w = max(max_thumb, 96)
    cell_h = max_thumb + label_h
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell_w, rows * cell_h), (32, 32, 32, 255))
    draw = ImageDraw.Draw(sheet) if ImageDraw else None
    for idx, (item, im) in enumerate(thumbs):
        x = (idx % columns) * cell_w
        y = (idx // columns) * cell_h
        ox = x + (cell_w - im.width) // 2
        oy = y + (max_thumb - im.height) // 2
        sheet.alpha_composite(im, (ox, oy))
        if draw:
            label = f"{item.name[:15]} {item.width}x{item.height} f{item.format_id}"
            draw.text((x + 4, y + max_thumb + 3), label, fill=(235, 235, 235, 255))
    return sheet
