"""Best-effort TEX0 manifest selection."""
from __future__ import annotations

from .decode import decode_tex0_prepared
from .tex0_layout import parse_tex0_candidates, prepare_tex0
from .types import Tex0Info
from .validate import score_tex0_candidate, score_tex0_candidate_structural


def _pick_tex0_candidate(
    candidates: list[Tex0Info],
    *,
    structural_only: bool,
) -> Tex0Info | None:
    if not candidates:
        return None

    if structural_only:
        def structural_score(info: Tex0Info) -> tuple[int, ...]:
            return score_tex0_candidate_structural(prepare_tex0(info))

        return max(candidates, key=structural_score)

    def decode_score(info: Tex0Info) -> tuple[int, ...]:
        prepared = prepare_tex0(info)
        images = decode_tex0_prepared(prepared, max_images=32, mode="all-palettes")
        return score_tex0_candidate(prepared, images)

    return max(candidates, key=decode_score)


def parse_tex0(data: bytes) -> Tex0Info | None:
    """Parse a TEX0 block from BTX0 or embedded NSBMD texture data.

    Nintendo DS documentation in the wild describes two closely related TEX0
    header layouts. Older DSM builds hard-coded one of them, which meant some
    real Pokémon BMD0 embedded TEX0 blocks exposed texture names but failed to
    reach the palette/image data. This parser tries both layouts and returns the
    richest structurally valid result.
    """
    return _pick_tex0_candidate(parse_tex0_candidates(data), structural_only=False)


def parse_tex0_manifest(data: bytes, *, embedded: bool = False) -> Tex0Info | None:
    """Parse TEX0 metadata for dictionary indexing (texture names, palettes).

    Uses the same decode-based layout selection as :func:`parse_tex0` so the ROM
    texture dictionary index matches model texture resolution and guided decode.
    The ``embedded`` flag is kept for callers but no longer changes behaviour.
    """
    del embedded
    return parse_tex0(data)
