"""ROM-platform feature flags — keep NDS-only tools out of other islands."""
from __future__ import annotations

EASYFIND_PLATFORM_IDS: frozenset[str] = frozenset({"nds"})
TEXTURE_INDEX_PLATFORM_IDS: frozenset[str] = frozenset({"nds"})


def supports_easyfind(rom_platform_id: str | None) -> bool:
    return (rom_platform_id or "nds") in EASYFIND_PLATFORM_IDS


def supports_texture_index(rom_platform_id: str | None) -> bool:
    return (rom_platform_id or "nds") in TEXTURE_INDEX_PLATFORM_IDS
