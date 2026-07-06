from __future__ import annotations

from ....core.modules.protocols import PlatformToolkitModule


class ThreedsToolkitModule:
    platform_id = "3ds"

    def install_ui(self, window: object) -> None:
        from ....ui.threeds_bulk_export import install_threeds_bulk_export_ui

        install_threeds_bulk_export_ui(window)
