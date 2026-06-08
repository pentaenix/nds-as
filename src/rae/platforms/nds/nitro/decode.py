"""High-level TEX0/BTX0 image decode pipelines."""
from __future__ import annotations

from .formats import decode_texture
from .palette_match import palette_options_for_texture
from .tex0_layout import parse_tex0_candidates, prepare_tex0
from .types import DecodedImage, GuidedDecodeReport, PaletteEntry, Tex0Info, TextureDecodeFailure, TextureEntry
from .validate import format_decode_failure, score_tex0_candidate, validate_texture_ranges

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

def decode_tex0_prepared(tex0: Tex0Info, *, max_images: int = 128, mode: str = "resolved") -> list[DecodedImage]:
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
        images = decode_tex0_prepared(prepared, max_images=max_images, mode=mode)
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


def guided_failures_for_candidate(
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

