from __future__ import annotations

from ....core.modules.protocols import AudioModule
from ....core.modules.types import ExportRoute, PreviewContext, PreviewRoute
from ....scanner import Asset


class MobileAudioModule:
    platform_id = "mobile"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        return False

    def export_route(self, asset: Asset) -> ExportRoute | None:
        if asset.magic in {"MOBL", "UNITY", "ABA", "HOME"}:
            return ExportRoute.MOBILE_RAW
        return None
