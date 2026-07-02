from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from .registry import get_platform_modules, modules_for_asset
from .types import ExportRoute, PreviewContext, PreviewRoute, ProfileSummary

if TYPE_CHECKING:
    from ..assets import Asset


class PlatformDispatch:
    """Thin router: UI calls this instead of branching on asset magic."""

    @staticmethod
    def for_rom_path(path: str | Path) -> str:
        from ...platforms import platform_for_path

        platform = platform_for_path(Path(path))
        return platform.id if platform is not None else "nds"

    @staticmethod
    def scan(
        path: str | Path,
        *,
        rom_platform_id: str,
        deep_scan: bool = False,
        progress=None,
    ) -> list:
        modules = get_platform_modules(rom_platform_id)
        assets = modules.scan.scan_rom(path, deep_scan=deep_scan, progress=progress)
        return modules.scan.post_scan(assets)

    @staticmethod
    def load_profile(source_path: Path, *, rom_platform_id: str) -> ProfileSummary:
        return get_platform_modules(rom_platform_id).scan.load_profile(source_path)

    @staticmethod
    def supports_texture_library_warmup(*, rom_platform_id: str) -> bool:
        return get_platform_modules(rom_platform_id).scan.supports_texture_library_warmup()

    @staticmethod
    def preview_route(asset, *, rom_platform_id: str | None) -> PreviewRoute:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        for probe in (
            modules.model.preview_route,
            modules.texture.preview_route,
            modules.audio.preview_route,
        ):
            route = probe(asset)
            if route is not None:
                return route
        return PreviewRoute.UNSUPPORTED

    @staticmethod
    def preview(ctx: PreviewContext, asset, *, rom_platform_id: str | None) -> bool:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        for module in (modules.model, modules.texture, modules.audio):
            if module.preview_route(asset) is None:
                continue
            if module.preview(ctx, asset):
                return True
        return False

    @staticmethod
    def asset_details(asset, *, rom_platform_id: str | None) -> str | None:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.details.asset_details(asset)

    @staticmethod
    def preview_status_lines(asset, *, window: object, rom_platform_id: str | None) -> list[str]:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.details.preview_status_lines(asset, window=window)

    @staticmethod
    def supports_model_inspector(asset, *, rom_platform_id: str | None) -> bool:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.model.supports_model_inspector(asset)

    @staticmethod
    def install_toolkit(window: object, *, rom_platform_id: str | None = None) -> None:
        from ...core.registry import active_platforms
        from .platform_boundaries import TOOLKIT_PLATFORM_IDS

        seen: set[str] = set()
        for platform in active_platforms():
            if platform.id not in TOOLKIT_PLATFORM_IDS:
                continue
            if platform.id in seen:
                continue
            seen.add(platform.id)
            modules = get_platform_modules(platform.id)
            if modules.toolkit is not None:
                modules.toolkit.install_ui(window)

    @staticmethod
    def export_route(asset, *, rom_platform_id: str | None) -> ExportRoute:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        route = modules.audio.export_route(asset)
        if route is not None:
            return route
        pid = asset_platform_id(asset, rom_platform_id=rom_platform_id)
        _DEFAULT_EXPORT: dict[str, ExportRoute] = {
            "nds": ExportRoute.STANDARD,
            "mobile": ExportRoute.RAW_BUNDLE,
        }
        return _DEFAULT_EXPORT.get(pid, ExportRoute.UNSUPPORTED)


    @staticmethod
    def export_options_for(asset, *, rom_platform_id: str | None) -> list[tuple[str, str, str]]:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.export.export_options_for(asset)

    @staticmethod
    def folder_export_options(*, rom_platform_id: str | None) -> list[tuple[str, str, str]]:
        pid = rom_platform_id or "nds"
        return get_platform_modules(pid).export.folder_export_options()

    @staticmethod
    def requires_apicula_for_folder_mode(mode: str, *, rom_platform_id: str | None) -> bool:
        pid = rom_platform_id or "nds"
        return get_platform_modules(pid).export.requires_apicula_for_folder_mode(mode)

    @staticmethod
    def export_folder_asset(host: object, asset, mode: str, staging: Path, *, rom_platform_id: str | None) -> int:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.export.export_folder_asset(host, asset, mode, staging)

    @staticmethod
    def run_export_choice(host: object, asset, choice: str, out: Path, *, rom_platform_id: str | None) -> list[Path]:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.export.run_export_choice(host, asset, choice, out)

    @staticmethod
    def export_blender_bundle(host: object, asset, out_dir: Path, *, rom_platform_id: str | None) -> tuple[bool, str]:
        modules = modules_for_asset(asset, rom_platform_id=rom_platform_id)
        return modules.export.export_blender_bundle(host, asset, out_dir)


def dispatch_for_path(path: str | Path) -> PlatformDispatch:
    return PlatformDispatch()


def dispatch_for_asset(asset, *, rom_platform_id: str | None = None):
    return modules_for_asset(asset, rom_platform_id=rom_platform_id)


from .registry import asset_platform_id  # noqa: E402
