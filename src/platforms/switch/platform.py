"""Nintendo Switch — register in core/registry.all_platforms()."""
from __future__ import annotations

from ...core.registry import Platform
from .rom import scan_switch_rom_path


Switch_PLATFORM = Platform(
    id="switch",
    label="Nintendo Switch",
    rom_extensions=(".nsp", ".xci"),
    status="active",
    scan_rom_path=scan_switch_rom_path,
)
