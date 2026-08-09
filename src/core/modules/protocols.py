from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from ..assets import Asset
from .types import ExportRoute, PreviewContext, PreviewRoute, ProfileSummary, Progress


class ScanModule(Protocol):
    platform_id: str

    def scan_rom(
        self,
        path: str | Path,
        *,
        deep_scan: bool = False,
        progress: Progress | None = None,
    ) -> list[Asset]: ...

    def post_scan(self, assets: list[Asset]) -> list[Asset]: ...

    def load_profile(self, source_path: Path) -> ProfileSummary: ...

    def supports_texture_library_warmup(self) -> bool: ...


class ModelModule(Protocol):
    platform_id: str

    def preview_route(self, asset: Asset) -> PreviewRoute | None: ...

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool: ...

    def supports_model_inspector(self, asset: Asset) -> bool: ...

    def home_package_stub_message(self, asset: Asset) -> str | None: ...


class TextureModule(Protocol):
    platform_id: str

    def preview_route(self, asset: Asset) -> PreviewRoute | None: ...

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool: ...

    def asset_details_extension(self, asset: Asset) -> str | None: ...


class AudioModule(Protocol):
    platform_id: str

    def preview_route(self, asset: Asset) -> PreviewRoute | None: ...

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool: ...

    def export_route(self, asset: Asset) -> ExportRoute | None: ...


class DetailsModule(Protocol):
    platform_id: str

    def asset_details(self, asset: Asset) -> str | None: ...

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]: ...


class ExportModule(Protocol):
    platform_id: str

    def folder_export_options(self) -> list[tuple[str, str, str]]: ...

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]: ...

    def requires_apicula_for_folder_mode(self, mode: str) -> bool: ...

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int: ...

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]: ...

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]: ...


class PlatformToolkitModule(Protocol):
    platform_id: str

    def install_ui(self, window: object) -> None: ...
