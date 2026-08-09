"""Nintendo 3DS — registered in core/registry.all_platforms()."""
from __future__ import annotations

from ...core.registry import Platform
from .rom import scan_threeds_rom_path

THREEDS_PLATFORM = Platform(
    id="3ds",
    label="Nintendo 3DS",
    rom_extensions=(".3ds", ".cci", ".cxi"),
    status="active",
    scan_rom_path=scan_threeds_rom_path,
    mapping_platform="3ds",
)
