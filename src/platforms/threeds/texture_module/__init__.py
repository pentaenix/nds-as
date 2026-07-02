from __future__ import annotations

from ....core.assets import Asset
from ....core.modules.types import PreviewContext, PreviewRoute
from ..rom import load_descriptor
from ..service import decode_sprite_png, load_textures


class ThreedsTextureModule:
    platform_id = "3ds"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic in {"GFTX", "FLIM"}:
            return PreviewRoute.TEXTURE_PNG
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        window = ctx.window
        descriptor = load_descriptor(asset)
        if not descriptor:
            return False
        try:
            if asset.magic == "FLIM":
                png = decode_sprite_png(descriptor)
                out = window.preview_temp / f"{asset.asset_id}.png"
                out.write_bytes(png)
                window.preview.show_image_path(out, f"3DS sprite: {asset.virtual_path}")
                return True
            if asset.magic == "GFTX":
                textures = load_textures(descriptor)
                if not textures:
                    window.preview.show_message(
                        f"No decodable GFTextures in this slot.\n\n{asset.virtual_path}"
                    )
                    return True
                shown = None
                for tex in textures:
                    out = window.preview_temp / f"{asset.asset_id}_{tex.name.replace('.tga','')}.png"
                    out.write_bytes(tex.to_png())
                    if shown is None:
                        shown = out
                window.preview.show_image_path(
                    shown,
                    f"3DS textures ({len(textures)} maps, normal set): {asset.virtual_path}",
                )
                if hasattr(window, "_update_status"):
                    window._update_status(
                        f"Decoded {len(textures)} texture(s); use Export for normal + shiny PNGs"
                    )
                return True
        except Exception as exc:
            window.preview.show_message(f"3DS texture preview failed:\n{exc}")
            return True
        return False

    def asset_details_extension(self, asset: Asset) -> str | None:
        return None
