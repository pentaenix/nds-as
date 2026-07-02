from __future__ import annotations

from ....core.modules.protocols import DetailsModule
from ....scanner import Asset


class NdsDetailsModule:
    platform_id = "nds"

    def asset_details(self, asset: Asset) -> str | None:
        return None

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        if asset.magic != "BMD0":
            return []
        lines = ["", "Model texture status"]
        if hasattr(window, "_model_preview_policy"):
            policy = window._model_preview_policy()
            lines.append(f"  Preview quality: {policy.label} ({policy.summary()})")
        pinned = window._pinned_texture_asset() if hasattr(window, "_pinned_texture_asset") else None
        lines.append(f"  Pinned external texture: {pinned.virtual_path if pinned else 'none'}")
        fallback_count = getattr(window, "_preview_fallback_count_by_asset_id", {}).get(asset.asset_id, 0)
        if fallback_count:
            lines.append(f"  RAE decoded preview/export texture PNGs: {fallback_count}")
        report = getattr(window, "_last_texture_resolve_report", {}).get(asset.asset_id, "")
        if "embedded TEX0 decoded" in report:
            lines.append("  Embedded NSBMD texture: decoded and available as RAE fallback PNGs")
        elif "embedded TEX0 texture block found" in report:
            lines.append("  Embedded NSBMD texture: detected; Set Textures can decode/trace it")
        status = getattr(window, "_preview_status_by_asset_id", {}).get(asset.asset_id)
        if status:
            lines.append(f"  {status}")
        lines.append("")
        lines.append("  RAE auto-resolves textures on preview. Open Texture Assigner to match textures to model parts.")
        saved = len(getattr(window, "_texture_assignments", {}).get(asset.asset_id, {}))
        if saved:
            lines.append(f"  Manual texture assignments saved in session: {saved} part(s)")
        if hasattr(window, "_texture_sequence_summary"):
            seq_summary = window._texture_sequence_summary(asset)
            if seq_summary:
                lines.append(f"  {seq_summary}")
                lines.append("  Use Animation States and ▶ in the viewport for flipbook playback.")
        return lines
