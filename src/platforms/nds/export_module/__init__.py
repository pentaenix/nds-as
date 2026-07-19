from __future__ import annotations

from pathlib import Path

from ....core.assets import Asset
from ....core.modules.protocols import ExportModule
from . import service

__all__ = [
    "NdsExportModule",
    "build_nds_export_module",
    "discover_tile_candidates",
    "export_tile_candidates",
    "handle_tile_export_request",
    "tile_export_api_schema",
]


class NdsExportModule:
    platform_id = "nds"

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return list(service.FOLDER_EXPORT_OPTIONS)

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        return service.export_options_for(asset)

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return service.requires_apicula_for_folder_mode(mode)

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        return service.export_folder_asset(host, asset, mode, staging)  # type: ignore[arg-type]

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        return service.run_export_choice(host, asset, choice, out)  # type: ignore[arg-type]

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        return service.export_blender_bundle(host, asset, out_dir)  # type: ignore[arg-type]


def build_nds_export_module() -> ExportModule:
    return NdsExportModule()


def __getattr__(name: str):
    if name in {
        "discover_tile_candidates",
        "export_tile_candidates",
        "handle_tile_export_request",
        "tile_export_api_schema",
    }:
        from . import api

        return getattr(api, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
