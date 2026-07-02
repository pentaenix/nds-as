from __future__ import annotations

from ...core.registry import Platform
from .rom import scan_mobile_rom_path

MOBILE_PLATFORM = Platform(
    id="mobile",
    label="Mobile app ROM",
    rom_extensions=(".rom",),
    status="active",
    scan_rom_path=scan_mobile_rom_path,
    mapping_platform="mobile",
)
