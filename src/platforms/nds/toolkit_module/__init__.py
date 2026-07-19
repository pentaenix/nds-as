from __future__ import annotations


class NdsToolkitModule:
    platform_id = "nds"

    def install_ui(self, window: object) -> None:
        from ..inspector import install_nds_tile_extractor

        install_nds_tile_extractor(window)
