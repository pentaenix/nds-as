from __future__ import annotations

from ....core.modules.protocols import TextureModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....scanner import Asset


class MobileTextureModule:
    """Mobile textures are exported alongside model preview (HOME Cache / AssetStudio)."""

    platform_id = "mobile"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        return False

    def asset_details_extension(self, asset: Asset) -> str | None:
        return None
