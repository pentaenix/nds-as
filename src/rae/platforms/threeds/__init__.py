"""Nintendo 3DS — planned."""
from __future__ import annotations

from ...core.registry import Platform

THREEDS_PLATFORM = Platform(
    id="3ds",
    label="Nintendo 3DS",
    rom_extensions=(".3ds", ".cci", ".cxi"),
    status="planned",
)

__all__ = ["THREEDS_PLATFORM"]
