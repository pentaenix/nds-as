from __future__ import annotations

from ....core.modules.protocols import ModelModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....core.assets import Asset


class {{platform_class}}ModelModule:
    platform_id = "{{platform_id}}"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        return False

    def supports_model_inspector(self, asset: Asset) -> bool:
        return False

    def home_package_stub_message(self, asset: Asset) -> str | None:
        return None
