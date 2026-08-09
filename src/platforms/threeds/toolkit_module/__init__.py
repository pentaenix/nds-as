from __future__ import annotations

from ....core.modules.protocols import PlatformToolkitModule


class ThreedsToolkitModule:
    platform_id = "3ds"

    def install_ui(self, window: object) -> None:
        from ..lbx_ui import install_lbx_export_ui

        # Install LBX actions independently. A failure in the optional Pokémon
        # menu wiring must not prevent the LBX tools from appearing.
        install_lbx_export_ui(window)
        try:
            from ....ui.threeds_bulk_export import install_threeds_bulk_export_ui

            install_threeds_bulk_export_ui(window)
        except Exception as exc:
            update = getattr(window, "_update_status", None)
            if callable(update):
                update(f"3DS Pokémon bulk export menu could not be installed: {exc}")
