"""Usage extractor registry and orchestration."""
from __future__ import annotations

from typing import Callable, Protocol

from ...core.mapping import GameMapping
from ...scanner import Asset
from ..models import EasyFindAssetTag, EasyFindDocument, EasyFindLocation
from .pokemon_gen4 import extract_pokemon_gen4_usage
from .types import UsageExtractionResult


class UsageExtractor(Protocol):
    def can_extract(
        self,
        *,
        platform: str,
        mapping: GameMapping | None,
        assets: list[Asset],
    ) -> bool: ...

    def extract(
        self,
        document: EasyFindDocument,
        assets: list[Asset],
        mapping: GameMapping | None,
    ) -> UsageExtractionResult: ...


class _ProfileExtractor:
    def __init__(
        self,
        profile_id: str,
        *,
        platforms: frozenset[str] | None = None,
        handler: Callable[[EasyFindDocument, list[Asset], GameMapping | None], UsageExtractionResult],
    ) -> None:
        self.profile_id = profile_id
        self.platforms = platforms
        self.handler = handler

    def can_extract(
        self,
        *,
        platform: str,
        mapping: GameMapping | None,
        assets: list[Asset],
    ) -> bool:
        if mapping is None:
            return False
        profile = str(getattr(mapping, "usage_profile", "") or "")
        if profile != self.profile_id:
            return False
        if self.platforms is not None and platform not in self.platforms:
            return False
        return True

    def extract(
        self,
        document: EasyFindDocument,
        assets: list[Asset],
        mapping: GameMapping | None,
    ) -> UsageExtractionResult:
        return self.handler(document, assets, mapping)


class _NoOpExtractor:
    def can_extract(
        self,
        *,
        platform: str,
        mapping: GameMapping | None,
        assets: list[Asset],
    ) -> bool:
        return True

    def extract(
        self,
        document: EasyFindDocument,
        assets: list[Asset],
        mapping: GameMapping | None,
    ) -> UsageExtractionResult:
        return UsageExtractionResult()


_EXTRACTORS: list[UsageExtractor] = [
    _ProfileExtractor(
        "pokemon_gen4",
        platforms=frozenset({"nds"}),
        handler=extract_pokemon_gen4_usage,
    ),
    _NoOpExtractor(),
]


def extract_usage(
    document: EasyFindDocument,
    assets: list[Asset],
    mapping: GameMapping | None,
    *,
    platform: str = "nds",
) -> UsageExtractionResult:
    for extractor in _EXTRACTORS:
        if extractor.can_extract(platform=platform, mapping=mapping, assets=assets):
            result = extractor.extract(document, assets, mapping)
            if isinstance(result, UsageExtractionResult):
                return result
    return UsageExtractionResult()


def merge_usage_annotations(
    document: EasyFindDocument,
    extracted: UsageExtractionResult,
    *,
    preserve_manual: bool = True,
) -> None:
    """Apply extracted locations/tags, optionally keeping manual annotations."""
    manual_locations: list[EasyFindLocation] = []
    manual_tags: list[EasyFindAssetTag] = []
    if preserve_manual:
        extracted_ids = {loc.location_id for loc in extracted.locations}
        for loc in document.locations:
            meta = loc.metadata or {}
            if meta.get("manual") or loc.location_id not in extracted_ids:
                manual_locations.append(loc)
        extracted_tag_keys = {
            (tag.node_id, tag.location_id) for tag in extracted.tags if tag.location_id
        }
        for tag in document.asset_tags:
            key = (tag.node_id, tag.location_id)
            meta = tag.metadata or {}
            if meta.get("manual") or key not in extracted_tag_keys:
                manual_tags.append(tag)

    merged_locations = list(extracted.locations) + manual_locations
    merged_locations.sort(key=lambda loc: (loc.group, loc.order or 0, loc.name, loc.location_id))

    merged_tags = list(extracted.tags) + manual_tags
    merged_tags.sort(key=lambda tag: (tag.node_id, tag.location_id or ""))

    document.locations = merged_locations
    document.asset_tags = merged_tags
