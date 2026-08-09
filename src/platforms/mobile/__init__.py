"""Mobile app .rom source support."""

from .platform import MOBILE_PLATFORM
from .rom import MobileRomManifest, read_mobile_rom_manifest, resolve_mobile_rom_root, scan_mobile_rom_path

__all__ = [
    "MOBILE_PLATFORM",
    "MobileRomManifest",
    "build_mobile_modules",
    "read_mobile_rom_manifest",
    "resolve_mobile_rom_root",
    "scan_mobile_rom_path",
]


def __getattr__(name: str):
    if name == "build_mobile_modules":
        from .platform_modules import build_mobile_modules

        return build_mobile_modules
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
