"""Game Boy Color — planned."""
from __future__ import annotations

from ...core.registry import Platform

GBC_PLATFORM = Platform(
    id="gbc",
    label="Game Boy Color",
    rom_extensions=(".gbc",),
    status="planned",
)

__all__ = ["GBC_PLATFORM"]
