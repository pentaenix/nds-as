from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..assets import Asset
from .protocols import AudioModule, DetailsModule, ExportModule, ModelModule, PlatformToolkitModule, ScanModule, TextureModule

from .asset_magics import all_asset_magics


def asset_platform_id(asset: "Asset", *, rom_platform_id: str | None = None) -> str:
    magic = (asset.magic or "").upper()
    table = all_asset_magics()
    if magic in table:
        return table[magic]
    return rom_platform_id or "nds"


@dataclass(frozen=True, slots=True)
class PlatformModules:
    platform_id: str
    scan: ScanModule
    model: ModelModule
    texture: TextureModule
    audio: AudioModule
    details: DetailsModule
    export: ExportModule
    toolkit: PlatformToolkitModule | None = None


@lru_cache(maxsize=16)
def get_platform_modules(platform_id: str) -> PlatformModules:
    from .platform_boundaries import load_platform_modules_builder

    builder = load_platform_modules_builder(platform_id)
    if builder is not None:
        return builder()
    from ...platforms.stub.platform_modules import build_stub_modules

    return build_stub_modules(platform_id)


def modules_for_asset(asset: "Asset", *, rom_platform_id: str | None = None) -> PlatformModules:
    return get_platform_modules(asset_platform_id(asset, rom_platform_id=rom_platform_id))
