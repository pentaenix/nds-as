from __future__ import annotations

from ....core.modules.protocols import PlatformToolkitModule


class MobileToolkitModule:
    platform_id = "mobile"

    def install_ui(self, window: object) -> None:
        from ....ui.mobile_device_toolkit import install_mobile_device_toolkit
        from ....ui.mobile_model_preview import install_mobile_model_preview_tools

        install_mobile_device_toolkit(window)
        install_mobile_model_preview_tools(window)
