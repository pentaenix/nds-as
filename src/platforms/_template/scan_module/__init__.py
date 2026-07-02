from __future__ import annotations

from pathlib import Path

from ....core.modules.protocols import ScanModule
from ....core.modules.types import ProfileSummary, Progress
from ....core.assets import Asset
from ..rom import scan_{{platform_id}}_rom_path


class {{platform_class}}ScanModule:
    platform_id = "{{platform_id}}"

    def scan_rom(
        self,
        path: str | Path,
        *,
        deep_scan: bool = False,
        progress: Progress | None = None,
    ) -> list[Asset]:
        return scan_{{platform_id}}_rom_path(path, progress=progress, deep_scan=deep_scan)

    def post_scan(self, assets: list[Asset]) -> list[Asset]:
        return assets

    def load_profile(self, source_path: Path) -> ProfileSummary:
        return ProfileSummary(rom_title=source_path.stem, profile_text="{{platform_label}} profile (not implemented).")

    def supports_texture_library_warmup(self) -> bool:
        return False
