from __future__ import annotations

from ....core.assets import Asset
from ....core.modules.types import ExportRoute, PreviewContext, PreviewRoute


class ThreedsAudioModule:
    platform_id = "3ds"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        return False

    def export_route(self, asset: Asset) -> ExportRoute | None:
        return None
