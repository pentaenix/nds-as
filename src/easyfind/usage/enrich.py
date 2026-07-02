"""Bake-time enrichment: link assets to in-game map/area locations."""
from __future__ import annotations

from typing import Callable

from ...core.mapping import GameMapping
from ...scanner import Asset
from ..models import EasyFindDocument
from .registry import extract_usage, merge_usage_annotations

Progress = Callable[[str], None] | None


def enrich_document_with_usage(
    document: EasyFindDocument,
    assets: list[Asset],
    mapping: GameMapping | None,
    *,
    platform: str = "nds",
    progress: Progress = None,
) -> EasyFindDocument:
    if progress:
        progress("Extracting map usage links…")
    extracted = extract_usage(
        document,
        assets,
        mapping,
        platform=platform,
    )
    merge_usage_annotations(document, extracted, preserve_manual=True)
    document.build_info = dict(document.build_info)
    document.build_info["usage_location_count"] = len(document.locations)
    document.build_info["usage_tag_count"] = len(document.asset_tags)
    if progress and document.locations:
        progress(
            f"Linked {len(document.asset_tags):,} asset placement(s) "
            f"across {len(document.locations):,} map(s)…"
        )
    return document
