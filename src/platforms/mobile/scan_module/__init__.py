from __future__ import annotations

from pathlib import Path

from ....core.modules.protocols import ScanModule
from ....core.modules.types import ProfileSummary, Progress
from ....scanner import Asset
from ..rom import read_mobile_rom_manifest, resolve_mobile_rom_root, scan_mobile_rom_path


class MobileScanModule:
    platform_id = "mobile"

    def scan_rom(
        self,
        path: str | Path,
        *,
        deep_scan: bool = False,
        progress: Progress | None = None,
    ) -> list[Asset]:
        if progress:
            progress(f"Scanning mobile app ROM: {path}")
        return scan_mobile_rom_path(path, progress=progress)

    def post_scan(self, assets: list[Asset]) -> list[Asset]:
        return assets

    def load_profile(self, source_path: Path) -> ProfileSummary:
        from ...home.mapping import choose_mapping_for_mobile_source, mobile_profile_summary

        root = resolve_mobile_rom_root(source_path)
        manifest = read_mobile_rom_manifest(root)
        mapping = choose_mapping_for_mobile_source(
            package_id=manifest.package_id,
            title=manifest.source_label or manifest.app_name,
            source_stem=source_path.stem,
        )
        return ProfileSummary(
            rom_game_code="HOME",
            rom_title=manifest.app_name or source_path.stem,
            profile_text=mobile_profile_summary(
                mapping,
                package_id=manifest.package_id,
                app_name=manifest.app_name,
            ),
            current_mapping=mapping,
        )

    def supports_texture_library_warmup(self) -> bool:
        return False
