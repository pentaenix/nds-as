"""Game Boy Advance — planned."""
from __future__ import annotations

from ...core.registry import Platform

GBA_PLATFORM = Platform(
    id="gba",
    label="Game Boy Advance",
    rom_extensions=(".gba",),
    status="planned",
)

__all__ = ["GBA_PLATFORM"]
