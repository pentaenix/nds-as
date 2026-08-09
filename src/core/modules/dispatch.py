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
    def sync_model_inspector(asset, *, window: object, rom_platform_id: str | None) -> None:
        modules = (
            modules_for_asset(asset, rom_platform_id=rom_platform_id)
            if asset is not None
            else get_platform_modules(rom_platform_id or "nds")
        )
        sync = getattr(modules.model, "sync_inspector", None)
        if callable(sync):
            sync(window, asset)

    @staticmethod
    def install_toolkit(window: object, *, rom_platform_id: str | None = None) -> None:
        from ...core.registry import active_platforms
        from .platform_boundaries import TOOLKIT_PLATFORM_IDS

        # Install 3DS last so Advanced-menu hooks are not invalidated by later toolkit work.
        install_order = ("mobile", "nds", "3ds")
        platforms_by_id = {platform.id: platform for platform in active_platforms()}
        seen: set[str] = set()
        for platform_id in install_order:
            if platform_id not in TOOLKIT_PLATFORM_IDS:
                continue
            platform = platforms_by_id.get(platform_id)
            if platform is None or platform.id in seen:
                continue
            seen.add(platform.id)
            modules = get_platform_modules(platform.id)
            if modules.toolkit is None:
                continue
            try:
                modules.toolkit.install_ui(window)
            except Exception as exc:
                update = getattr(window, "_update_status", None)
                if callable(update):
                    update(f"{platform.label} toolkit could not be installed: {exc}")
                else:
                    raise

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

    @staticmethod
    def supports_pokemon_bulk_export(
        *,
        rom_path: str | Path,
        rom_platform_id: str | None,
        assets: list,
        product_code: str | None = None,
    ) -> bool:
        if (rom_platform_id or "") != "3ds":
            return False
        return get_platform_modules("3ds").export.supports_pokemon_bulk_export(
            rom_path,
            assets,
            product_code=product_code,
        )

    @staticmethod
    def run_pokemon_bulk_export(
        host: object,
        assets: list,
        rom_path: str | Path,
        out_dir: Path,
        *,
        rom_platform_id: str | None,
        shiny: bool = False,
        progress=None,
    ):
        if (rom_platform_id or "") != "3ds":
            raise ValueError("Pokémon bulk export is only available for 3DS ROMs.")
        return get_platform_modules("3ds").export.run_pokemon_bulk_export(
            host,
            assets,
            rom_path,
            out_dir,
            shiny=shiny,
            progress=progress,
        )

    @staticmethod
    def preview_platform_id(rom_platform_id: str | None) -> str:
        return rom_platform_id or "nds"

    @staticmethod
    def material_policy_platform_id(rom_platform_id: str | None, *, asset_magic: str | None = None) -> str:
        if asset_magic == "GFMD":
            return "3ds"
        return PlatformDispatch.preview_platform_id(rom_platform_id)

    @staticmethod
    def build_web_preview_glb(
        source_glb: Path,
        *,
        rom_platform_id: str | None,
        mesh_labels: list[str],
        mesh_texture_paths: list,
        texture_by_name: dict[str, Path] | None = None,
        material_to_texture: dict[str, str] | None = None,
        stage_texture_paths: list[Path] | None = None,
        patcher: object | None = None,
    ) -> Path | None:
        pid = PlatformDispatch.preview_platform_id(rom_platform_id)
        if pid != "nds":
            return None
        from ...platforms.nds.preview.glb_patcher import PreviewGlbPatcher, build_web_preview_glb

        return build_web_preview_glb(
            source_glb,
            mesh_labels=mesh_labels,
            mesh_texture_paths=mesh_texture_paths,
            texture_by_name=texture_by_name,
            material_to_texture=material_to_texture,
            stage_texture_paths=stage_texture_paths,
            patcher=patcher if isinstance(patcher, PreviewGlbPatcher) else None,
        )

    @staticmethod
    def build_flipbook_preview_glbs(
        source_glb: Path,
        *,
        rom_platform_id: str | None,
        material_name: str,
        frame_paths: list[Path],
        mesh_labels: list[str],
        base_mesh_paths: list,
    ) -> list[Path]:
        pid = PlatformDispatch.preview_platform_id(rom_platform_id)
        if pid != "nds":
            return []
        from ...platforms.nds.preview.glb_patcher import PreviewGlbPatcher

        return PreviewGlbPatcher().build_flipbook_glbs(
            source_glb,
            material_name=material_name,
            frame_paths=frame_paths,
            mesh_labels=mesh_labels,
            base_mesh_paths=base_mesh_paths,
        )


def dispatch_for_path(path: str | Path) -> PlatformDispatch:
    return PlatformDispatch()


def dispatch_for_asset(asset, *, rom_platform_id: str | None = None):
    return modules_for_asset(asset, rom_platform_id=rom_platform_id)


from .registry import asset_platform_id  # noqa: E402
