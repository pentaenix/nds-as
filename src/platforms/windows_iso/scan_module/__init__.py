from __future__ import annotations

from pathlib import Path

from ....core.modules.protocols import ScanModule
from ....core.modules.types import ProfileSummary, Progress
from ....core.assets import Asset
from ..profiles import profile_hint_from_filename
from ..rom import scan_windows_iso_rom_path


class WindowsIsoScanModule:
    platform_id = "windows_iso"

    def scan_rom(
        self,
        path: str | Path,
        *,
        deep_scan: bool = False,
        progress: Progress | None = None,
    ) -> list[Asset]:
        return scan_windows_iso_rom_path(path, progress=progress, deep_scan=deep_scan)

    def post_scan(self, assets: list[Asset]) -> list[Asset]:
        return assets

    def load_profile(self, source_path: Path) -> ProfileSummary:
        profile = profile_hint_from_filename(source_path)
        if profile is None:
            return ProfileSummary(
                rom_title=source_path.stem,
                profile_text="Windows CD/ISO image (unrecognized profile)",
            )
        return ProfileSummary(
            rom_game_code=profile.profile_id,
            rom_title=profile.title,
            profile_text=f"Windows CD/ISO — {profile.title} ({profile.year})",
        )

    def supports_texture_library_warmup(self) -> bool:
        return False
