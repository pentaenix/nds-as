from __future__ import annotations

import json

from ....core.assets import Asset


class SwitchDetailsModule:
    platform_id = "switch"

    def _descriptor(self, asset: Asset) -> dict:
        try:
            return json.loads(asset.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def asset_details(self, asset: Asset) -> str | None:
        desc = self._descriptor(asset)
        kind = desc.get("type")
        if kind == "locked":
            return desc.get("message")
        if kind == "summary":
            return (
                f"Nintendo Switch ROM\n"
                f"RomFS files: {desc.get('romfs_files')}\n"
                f"Keys: {desc.get('keys_source')}"
            )
        if kind == "romfs_file":
            return (
                f"RomFS: {desc.get('romfs_path')}\n"
                f"Size: {desc.get('size')} bytes\n"
                "Use Export to decrypt and save the raw payload."
            )
        if kind == "trinity_file":
            return (
                f"TRPAK: {desc.get('trpak_path')}\n"
                f"Path: {desc.get('inner_path')}\n"
                f"Hash: {desc.get('file_hash', 0):#018x}"
            )
        return None

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        desc = self._descriptor(asset)
        if desc.get("type") == "locked":
            return ["Switch ROM locked — prod.keys required."]
        return []
