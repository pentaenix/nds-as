from __future__ import annotations

import json
from pathlib import Path

from ....core.assets import Asset
from ..service import export_raw


class SwitchExportModule:
    platform_id = "switch"

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            ("folder_raw", "ZIP: Raw assets", "Write extracted payloads for every asset in the folder."),
        ]

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        if asset.magic in {"SWRM", "SWLK"}:
            return []
        if asset.magic == "TRMD":
            return [
                ("glb", "GLB model", "Decode Trinity mesh data and export as GLB."),
                ("raw", "Original / raw asset", "Extract the packed TRMDL blob."),
            ]
        return [
            ("raw", "Original / raw asset", "Decrypt and save the selected asset."),
        ]

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return False

    def _descriptor(self, asset: Asset) -> dict:
        try:
            return json.loads(asset.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        descriptor = self._descriptor(asset)
        if descriptor.get("type") not in {"romfs_file", "trinity_file"}:
            return 0
        progress = getattr(host, "_update_status", None)
        written = export_raw(descriptor, staging, progress)
        return len(written)

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        descriptor = self._descriptor(asset)
        progress = getattr(host, "_update_status", None)
        if choice == "glb" and descriptor.get("type") == "trinity_file":
            from ..service import export_model_glb

            return export_model_glb(descriptor, out, progress)
        if descriptor.get("type") in {"romfs_file", "trinity_file"}:
            return export_raw(descriptor, out, progress)
        raise ValueError("This Switch row has no extractable payload.")

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        return False, "Blender bundle export is not available for Nintendo Switch yet."
