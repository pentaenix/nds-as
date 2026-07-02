from __future__ import annotations

from pathlib import Path

from ....core.modules.protocols import ExportModule
from ....core.assets import Asset


class {{platform_class}}ExportModule:
    platform_id = "{{platform_id}}"

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            ("folder_raw", "ZIP: Raw assets", "Write extracted payloads for every asset in the folder."),
        ]

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        return [
            ("raw", "Original / raw asset", "Save the selected asset as extracted."),
        ]

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return False

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        raise NotImplementedError("Implement {{platform_id}} folder export")

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        raise NotImplementedError("Implement {{platform_id}} export")

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        return False, "Blender bundle export is not available for {{platform_label}}."
