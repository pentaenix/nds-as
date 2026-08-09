"""Nintendo 3DS platform — active (decrypted .cci/.3ds/.cxi dumps)."""
from __future__ import annotations

from .platform import THREEDS_PLATFORM
from .rom import scan_threeds_rom_path

__all__ = ["THREEDS_PLATFORM", "build_threeds_modules", "scan_threeds_rom_path"]


def __getattr__(name: str):
    if name == "build_threeds_modules":
        from .platform_modules import build_threeds_modules

        return build_threeds_modules
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
