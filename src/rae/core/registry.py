"""Platform registry — one entry per supported console."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from .mapping import GameMapping, choose_mapping, load_mappings

PlatformStatus = Literal["active", "planned"]

ScanRomPath = Callable[..., list]
DetectProfile = Callable[..., object]


@dataclass(frozen=True, slots=True)
class Platform:
    id: str
    label: str
    rom_extensions: tuple[str, ...]
    status: PlatformStatus
    scan_rom_path: ScanRomPath | None = None
    detect_profile: DetectProfile | None = None
    mapping_platform: str | None = None

    @property
    def mapping_key(self) -> str:
        return self.mapping_platform or self.id

    def file_dialog_filter(self) -> str:
        exts = " ".join(f"*{ext}" for ext in self.rom_extensions)
        if self.status == "active":
            return f"{self.label} ROM ({exts})"
        return f"{self.label} ({exts}) — planned"


def platform_for_path(path: Path, platforms: dict[str, Platform] | None = None) -> Platform | None:
    registry = platforms or all_platforms()
    suffix = path.suffix.casefold()
    for platform in registry.values():
        if suffix in platform.rom_extensions:
            return platform
    return None


def active_platforms() -> list[Platform]:
    return [p for p in all_platforms().values() if p.status == "active"]


def all_platforms() -> dict[str, Platform]:
    from ..platforms.nds import NDS_PLATFORM
    from ..platforms.gba import GBA_PLATFORM
    from ..platforms.gbc import GBC_PLATFORM
    from ..platforms.gb import GB_PLATFORM
    from ..platforms.threeds import THREEDS_PLATFORM
    from ..platforms.mobile import MOBILE_PLATFORM

    return {
        p.id: p
        for p in (
            NDS_PLATFORM,
            GBA_PLATFORM,
            GBC_PLATFORM,
            GB_PLATFORM,
            THREEDS_PLATFORM,
            MOBILE_PLATFORM,
        )
    }


def choose_mapping_for_platform(
    platform: Platform,
    title: str,
    game_code: str,
    *,
    available: list[GameMapping] | None = None,
) -> GameMapping | None:
    maps = available if available is not None else load_mappings(platform=platform.mapping_key)
    return choose_mapping(title, game_code, available=maps)
