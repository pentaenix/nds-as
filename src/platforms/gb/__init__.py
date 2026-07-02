"""Game Boy — planned."""
from __future__ import annotations

from ...core.registry import Platform

GB_PLATFORM = Platform(
    id="gb",
    label="Game Boy",
    rom_extensions=(".gb",),
    status="planned",
)

__all__ = ["GB_PLATFORM"]
