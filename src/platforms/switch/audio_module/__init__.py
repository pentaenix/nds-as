from __future__ import annotations

from ....core.modules.protocols import AudioModule
from ....core.modules.types import ExportRoute, PreviewContext, PreviewRoute
from ....core.assets import Asset


class SwitchAudioModule:
    platform_id = "switch"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        return False

    def export_route(self, asset: Asset) -> ExportRoute | None:
        return None
