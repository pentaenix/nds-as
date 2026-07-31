"""Windows CD/ISO — register in core/registry.all_platforms()."""
from __future__ import annotations

from ...core.registry import Platform
from .rom import scan_windows_iso_rom_path


WindowsIso_PLATFORM = Platform(
    id="windows_iso",
    label="Windows CD/ISO",
    rom_extensions=(".iso",),
    status="active",
    scan_rom_path=scan_windows_iso_rom_path if "active" == "active" else None,
)
