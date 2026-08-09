from __future__ import annotations

from ....core.modules.protocols import TextureModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....core.assets import Asset


class {{platform_class}}TextureModule:
    platform_id = "{{platform_id}}"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        return False

    def asset_details_extension(self, asset: Asset) -> str | None:
        return None
