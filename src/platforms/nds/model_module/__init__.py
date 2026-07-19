from __future__ import annotations

from ....core.modules.protocols import ModelModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....scanner import Asset

_NDS_MODEL_MAGICS = frozenset({"BMD0"})


class NdsModelModule:
    platform_id = "nds"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic in _NDS_MODEL_MAGICS:
            return PreviewRoute.NDS_MODEL
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        window = ctx.window
        if asset.magic != "BMD0":
            return False
        window.convert_preview_selected(manual=ctx.manual, force=ctx.force)
        return True

    def supports_model_inspector(self, asset: Asset) -> bool:
        return asset.magic == "BMD0"

    def home_package_stub_message(self, asset: Asset) -> str | None:
        return None

    def sync_inspector(self, window: object, asset: Asset | None) -> None:
        from ..inspector import sync_nds_tile_extractor

        sync_nds_tile_extractor(window, asset)
