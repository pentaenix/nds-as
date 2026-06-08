"""Nintendo DS platform — active."""
from __future__ import annotations

from ...core.registry import Platform
from .profiles import detect_profile
from .scanner import scan_nds_path

NDS_PLATFORM = Platform(
    id="nds",
    label="Nintendo DS",
    rom_extensions=(".nds",),
    status="active",
    scan_rom_path=scan_nds_path,
    detect_profile=detect_profile,
    mapping_platform="nds",
)

__all__ = ["NDS_PLATFORM", "detect_profile", "scan_nds_path"]
