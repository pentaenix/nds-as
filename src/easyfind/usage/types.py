"""Usage extraction result types."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..models import EasyFindAssetTag, EasyFindLocation


@dataclass
class UsageExtractionResult:
    locations: list[EasyFindLocation] = field(default_factory=list)
    tags: list[EasyFindAssetTag] = field(default_factory=list)
