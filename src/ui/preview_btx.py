"""BTX0 fallback PNG helpers for model preview."""
from __future__ import annotations

from pathlib import Path

from ..nitro_textures import decode_btx_images, save_decoded_images
from ..scanner import Asset

def write_btx_preview_images(texture_assets: list[Asset], out_dir: Path, *, max_textures: int = 2) -> list[Path]:
    """Decode candidate BTX0 files to PNGs for RAE's own preview fallback.

    This does not replace apicula output. It gives the preview widget actual
    bitmap data when the converted GLB has UV/material slots but no embedded
    image.
    """
    written: list[Path] = []
    target = out_dir / "dsm_decoded_textures"
    for texture in texture_assets[:max_textures]:
        try:
            images = decode_btx_images(texture.data, max_images=16, mode="all-palettes")
            if not images:
                continue
            written.extend(save_decoded_images(images, target / texture.asset_id, prefix=Path(texture.virtual_path).stem))
        except Exception:
            continue
    return written
