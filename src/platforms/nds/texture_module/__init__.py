from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ....core.modules.protocols import TextureModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....scanner import Asset

_NDS_2D_MAGICS = frozenset({"RGCN", "RLCN", "RCSN", "RECN", "RNAN"})
_NDS_TEXTURE_MAGICS = frozenset({"BTX0"})


class NdsTextureModule:
    platform_id = "nds"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic in _NDS_TEXTURE_MAGICS:
            return PreviewRoute.NDS_TEXTURE_BTX0
        if asset.magic in _NDS_2D_MAGICS:
            return PreviewRoute.NDS_TEXTURE_2D
        if asset.magic == "PNG" or asset.data.startswith(b"\x89PNG"):
            return PreviewRoute.NDS_PNG
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        window = ctx.window
        route = self.preview_route(asset)
        if route == PreviewRoute.NDS_TEXTURE_BTX0:
            if getattr(asset, "is_texture_slot", False) and asset.texture_slot:
                window._preview_btx0_texture(asset, asset.texture_slot)
                return True
            texture_name = window._selected_btx0_texture_name or window._preview_btx0_texture_name(asset)
            if texture_name:
                window._preview_btx0_texture(asset, texture_name)
                return True
            window.preview.show_message(
                f"No named texture entries found in this NSBTX archive yet.\n\n"
                f"{asset.virtual_path}\n\n"
                "Use Export Selected → readable for a full decode attempt."
            )
            window._update_status(f"BTX0 archive has no dictionary names: {asset.virtual_path}")
            return True
        if route == PreviewRoute.NDS_TEXTURE_2D:
            window._preview_decodable_images(asset, asset.kind)
            return True
        if route == PreviewRoute.NDS_PNG:
            out = window.preview_temp / f"{asset.asset_id}.png"
            out.write_bytes(asset.data)
            window.preview.show_image_path(out, f"PNG image: {asset.virtual_path}")
            window._update_status(f"Previewing PNG image {asset.virtual_path}")
            return True
        if ctx.manual:
            QMessageBox.information(
                window,
                "No visual decoder yet",
                f"RAE can export this asset raw, but does not have a visual preview for {asset.magic or asset.kind} yet.",
            )
        else:
            window.preview.show_message(
                f"No visual preview decoder yet for this asset.\n\n{asset.kind} / {asset.magic}\n{asset.virtual_path}"
            )
        return False

    def asset_details_extension(self, asset: Asset) -> str | None:
        return None
