"""Nintendo DS platform — active."""
from __future__ import annotations

from .platform import NDS_PLATFORM
from .profiles import detect_profile
from .scanner import scan_nds_path

__all__ = ["NDS_PLATFORM", "build_nds_modules", "detect_profile", "scan_nds_path"]


def __getattr__(name: str):
    if name == "build_nds_modules":
        from .platform_modules import build_nds_modules

        return build_nds_modules
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
