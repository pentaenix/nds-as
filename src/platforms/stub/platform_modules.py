from __future__ import annotations

from pathlib import Path

from ...core.modules.registry import PlatformModules
from ...core.modules.types import ProfileSummary, Progress
from ...scanner import Asset


class _StubScanModule:
    def __init__(self, platform_id: str) -> None:
        self.platform_id = platform_id

    def scan_rom(self, path: str | Path, *, deep_scan: bool = False, progress: Progress | None = None) -> list[Asset]:
        raise NotImplementedError(f"{self.platform_id} ROM scanning is not implemented yet.")

    def post_scan(self, assets: list[Asset]) -> list[Asset]:
        return assets

    def load_profile(self, source_path: Path) -> ProfileSummary:
        return ProfileSummary(rom_title=source_path.stem, profile_text=f"{self.platform_id} support is planned.")

    def supports_texture_library_warmup(self) -> bool:
        return False


class _StubModelModule:
    def __init__(self, platform_id: str) -> None:
        self.platform_id = platform_id

    def preview_route(self, asset: Asset):
        return None

    def preview(self, ctx, asset: Asset) -> bool:
        return False

    def supports_model_inspector(self, asset: Asset) -> bool:
        return False

    def home_package_stub_message(self, asset: Asset) -> str | None:
        return None


class _StubTextureModule:
    def __init__(self, platform_id: str) -> None:
        self.platform_id = platform_id

    def preview_route(self, asset: Asset):
        return None

    def preview(self, ctx, asset: Asset) -> bool:
        return False

    def asset_details_extension(self, asset: Asset) -> str | None:
        return None


class _StubAudioModule:
    def __init__(self, platform_id: str) -> None:
        self.platform_id = platform_id

    def preview_route(self, asset: Asset):
        return None

    def preview(self, ctx, asset: Asset) -> bool:
        return False

    def export_route(self, asset: Asset):
        return None


class _StubDetailsModule:
    def __init__(self, platform_id: str) -> None:
        self.platform_id = platform_id

    def asset_details(self, asset: Asset) -> str | None:
        return None

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        return []


class _StubExportModule:
    def __init__(self, platform_id: str) -> None:
        self.platform_id = platform_id

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return []

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        return [("raw", "Original / raw asset", f"Save asset bytes ({self.platform_id} export is not implemented yet).")]

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return False

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        raise NotImplementedError(f"{self.platform_id} folder export is not implemented yet.")

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        raise NotImplementedError(f"{self.platform_id} export is not implemented yet.")

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        return False, f"Blender bundle export is not available for {self.platform_id}."


def build_stub_modules(platform_id: str) -> PlatformModules:
    return PlatformModules(
        platform_id=platform_id,
        scan=_StubScanModule(platform_id),
        model=_StubModelModule(platform_id),
        texture=_StubTextureModule(platform_id),
        audio=_StubAudioModule(platform_id),
        details=_StubDetailsModule(platform_id),
        export=_StubExportModule(platform_id),
        toolkit=None,
    )
