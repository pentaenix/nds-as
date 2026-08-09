from __future__ import annotations

import json

from ....core.modules.protocols import DetailsModule
from ....core.assets import Asset


class WindowsIsoDetailsModule:
    platform_id = "windows_iso"

    def asset_details(self, asset: Asset) -> str | None:
        descriptor = self._descriptor(asset)
        kind = descriptor.get("type")
        if kind in {"locked", "information"}:
            return str(descriptor.get("message") or "Windows ISO information")
        if kind == "summary":
            counts = descriptor.get("format_counts") or {}
            return (
                f"{descriptor.get('game_title', 'Windows game')}\n"
                f"InstallShield catalog: {descriptor.get('catalog_records', 0):,} records\n"
                f"Browsable models: {descriptor.get('model_rows', 0):,}\n"
                f"SMO: {counts.get('SMO', 0):,}; AM1: {counts.get('AM1', 0):,}; "
                f"AM2/AM3: {counts.get('AM2', 0) + counts.get('AM3', 0):,}"
            )
        if kind == "model":
            model = descriptor.get("model") or {}
            animations = descriptor.get("animations") or []
            textures = descriptor.get("textures") or []
            shadows = descriptor.get("shadows") or []
            lods = descriptor.get("lods") or []
            animation_sets = {str(row.get("path", "")).rsplit("/", 1)[-1].rsplit(".", 1)[0].casefold()
                              for row in animations if row.get("format") == "AM2"}
            return (
                f"{model.get('format', 'Windows')} model: {model.get('path', '?')}\n"
                f"Catalog size: {model.get('size', 0):,} bytes\n"
                f"Animation source records: {len(animations):,}\n"
                f"Linked AM2 animation sets: {len(animation_sets):,}\n"
                f"Catalog texture hints: {len(textures):,} (embedded names resolve on load)\n"
                f"Attached lower-detail meshes: {len(lods):,}\n"
                f"Associated shadows: {len(shadows):,}\n"
                "Fast preview loads four clips; use Load all animations for the complete model.\n"
                "Payload extraction is deferred until preview or export."
            )
        return None

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        descriptor = self._descriptor(asset)
        if descriptor.get("type") == "locked":
            return ["Windows ISO setup is incomplete; see asset details."]
        return []

    @staticmethod
    def _descriptor(asset: Asset) -> dict:
        try:
            value = json.loads(asset.data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}
