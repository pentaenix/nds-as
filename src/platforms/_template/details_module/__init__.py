from __future__ import annotations

from ....core.modules.protocols import DetailsModule
from ....core.assets import Asset


class {{platform_class}}DetailsModule:
    platform_id = "{{platform_id}}"

    def asset_details(self, asset: Asset) -> str | None:
        return None

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        return []
