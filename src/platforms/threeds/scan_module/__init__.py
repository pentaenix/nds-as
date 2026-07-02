from __future__ import annotations

from pathlib import Path

from ....core.assets import Asset
from ....core.modules.types import ProfileSummary, Progress
from ..container import ThreedsImage
from ..rom import scan_threeds_rom_path


class ThreedsScanModule:
    platform_id = "3ds"

    def scan_rom(
        self,
        path: str | Path,
        *,
        deep_scan: bool = False,
        progress: Progress | None = None,
    ) -> list[Asset]:
        return scan_threeds_rom_path(path, progress=progress)

    def post_scan(self, assets: list[Asset]) -> list[Asset]:
        return assets

    def load_profile(self, source_path: Path) -> ProfileSummary:
        title = source_path.stem
        product = ""
        try:
            with ThreedsImage(source_path) as image:
                part = image.main_partition()
                if part is not None:
                    product = part.product_code
        except Exception:
            pass
        text = f"Nintendo 3DS image ({product})" if product else "Nintendo 3DS image"
        return ProfileSummary(rom_game_code=product, rom_title=title, profile_text=text)

    def supports_texture_library_warmup(self) -> bool:
        return False
