from __future__ import annotations

import json

from ....core.modules.protocols import DetailsModule
from ....scanner import Asset


class MobileDetailsModule:
    platform_id = "mobile"

    def asset_details(self, asset: Asset) -> str | None:
        if asset.magic == "HOME":
            from ...home.ui import home_asset_details

            return home_asset_details(asset)
        if asset.magic not in {"MOBL", "UNITY", "ABA"}:
            return None
        lines = [
            f"Name: {getattr(asset, 'mapping_label', '') or asset.virtual_path}",
            f"File: {asset.virtual_path.rsplit('/', 1)[-1]}",
            f"Path: {asset.virtual_path}",
            f"Kind: {asset.kind}",
            f"Magic: {asset.magic}",
            "",
            "Mobile app ROM asset",
        ]
        try:
            meta = json.loads(asset.data.decode("utf-8"))
        except Exception:
            meta = {}
        if meta:
            mobile_rom = meta.get("mobileRom") or meta.get("manifest") or {}
            if mobile_rom:
                lines.append(f"Package: {mobile_rom.get('packageId') or mobile_rom.get('package_id') or 'unknown'}")
                lines.append(f"App: {mobile_rom.get('appName') or mobile_rom.get('app_name') or 'unknown'}")
            if meta.get("classification"):
                lines.append(f"Classification: {meta.get('classification')}")
            if meta.get("local_path"):
                lines.append(f"Local path: {meta.get('local_path')}")
        if asset.magic == "UNITY":
            lines.append(
                "Unity bundle candidate. Install UnityPy or AssetStudioModCLI for mesh/texture export."
            )
        elif asset.magic == "ABA":
            lines.append(
                "Encrypted HOME .aba package. RAE decrypts the bundle header and previews from HOME Cache when available."
            )
            lines.append(
                "Open the Pokémon once in HOME to populate external_files/files/Cache, then re-extract the ROM."
            )
        return "\n".join(lines)

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        if asset.magic not in {"ABA", "HOME", "UNITY"}:
            return []
        preview = getattr(window, "preview", None)
        fallback_count = len(getattr(preview, "_fallback_texture_paths", []) or []) if preview else 0
        lines = ["", "HOME model preview status"]
        if fallback_count:
            lines.append(f"  Exported HOME textures available: {fallback_count}")
            lines.append("  Open Texture Sheet to inspect every _col/_emi map for this species.")
            lines.append("  Use Texture Assigner to swap body/eye maps on the mesh.")
        else:
            lines.append("  Textures export from HOME Cache when you preview a species opened in-app.")
            lines.append("  View the Pokémon once in HOME, re-extract the ROM, then preview again.")
        lines.append("  One in-app view per species fills Cache for mesh + textures + animations together.")
        status = getattr(window, "_preview_status_by_asset_id", {}).get(asset.asset_id)
        if status:
            lines.append(f"  {status}")
        return lines
