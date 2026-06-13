"""EasyFind model thumbnails — uses the shared viewport preview pipeline."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ..model_preview import prepare_model_preview, render_model_preview_snapshot
from ..platforms.nds.exporter import find_apicula
from ..platforms.nds.scanner import Asset
from ..texture_library import TextureLibrary
from .color_buckets import MODEL_THUMB_BACKGROUND_RGB

if TYPE_CHECKING:
    from ..model_preview.web_snapshot import ModelWebSnapshotService

# Match embedded bake viewer size — capture here, then downscale for storage.
CAPTURE_RENDER_SIZE = 256
OUTPUT_THUMB_SIZE = 128


def finalize_easyfind_model_thumbnail(
    png_bytes: bytes,
    *,
    output_size: int = OUTPUT_THUMB_SIZE,
) -> bytes:
    """Downscale a three.js capture with nearest-neighbor for crisp pixel-art models."""
    import io

    from PIL import Image

    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    background = Image.new("RGBA", image.size, (*MODEL_THUMB_BACKGROUND_RGB, 255))
    background.alpha_composite(image)
    image = background.convert("RGB")
    if image.size != (output_size, output_size):
        image = image.resize((output_size, output_size), Image.Resampling.NEAREST)
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


Progress = Callable[[str], None]


def render_glb_orthographic_thumbnail(
    glb_path: Path,
    *,
    width: int = 128,
    height: int = 128,
    yaw_deg: float = 35.0,
    pitch_deg: float = 28.0,
    zoom_factor: float = 1.0,
    texture_by_name: dict[str, Path] | None = None,
    material_to_texture: dict[str, str] | None = None,
    texture_bind_order: list[str] | None = None,
    fallback_paths: list[Path] | None = None,
) -> tuple[bytes | None, int | None, int | None]:
    """Render a patched preview GLB using the shared viewport snapshot path."""
    from ..model_preview.pipeline import ModelPreviewBundle
    from ..glb_preview_textures import parse_glb_mesh_parts, build_mesh_texture_paths_for_glb_parts

    parts = parse_glb_mesh_parts(glb_path)
    mesh_labels = tuple(part.label for part in parts)
    mesh_texture_paths = tuple(
        build_mesh_texture_paths_for_glb_parts(
            parts,
            glb_path=glb_path,
            texture_by_name=texture_by_name or {},
            material_to_texture=material_to_texture or {},
            texture_bind_order=texture_bind_order or [],
            fallback_paths=list(fallback_paths or ()),
        )
    )
    bundle = ModelPreviewBundle(
        source_glb=glb_path,
        patched_glb=glb_path,
        mesh_labels=mesh_labels,
        mesh_texture_paths=mesh_texture_paths,
        texture_by_name=dict(texture_by_name or {}),
        material_to_texture=dict(material_to_texture or {}),
        texture_bind_order=tuple(texture_bind_order or ()),
        fallback_paths=tuple(fallback_paths or ()),
    )
    return render_model_preview_snapshot(
        bundle,
        width=width,
        height=height,
        yaw_deg=yaw_deg,
        pitch_deg=pitch_deg,
        zoom_factor=zoom_factor,
    )


def bake_bmd0_thumbnail_bytes(
    asset: Asset,
    all_assets: list[Asset],
    *,
    width: int = 128,
    height: int = 128,
    texture_library: TextureLibrary | None = None,
    progress: Progress | None = None,
    web_snapshot: ModelWebSnapshotService | None = None,  # noqa: F821
) -> tuple[bytes | None, int | None, int | None]:
    """Bake a model thumbnail using the viewport three.js renderer when available."""
    if asset.magic.upper() != "BMD0" or not asset.data:
        return None, None, None
    if not find_apicula():
        return None, None, None

    with tempfile.TemporaryDirectory(prefix="rae_ef_thumb_") as tmp:
        out_dir = Path(tmp)
        if progress:
            progress(f"Rendering model thumbnail: {asset.virtual_path}")
        bundle = prepare_model_preview(
            asset,
            all_assets,
            out_dir,
            texture_library=texture_library,
            progress=progress,
        )
        if bundle is None:
            return None, None, None

        web_session = web_snapshot is not None and web_snapshot.is_available()
        if web_snapshot is not None and not web_session:
            if progress:
                progress("Three.js snapshot viewer not ready.")
            return None, None, None
        if web_session:
            if progress:
                progress(f"Three.js snapshot: {asset.virtual_path}")
            png = web_snapshot.capture_blocking(
                bundle.patched_glb,
                CAPTURE_RENDER_SIZE,
                CAPTURE_RENDER_SIZE,
                output_width=width,
                output_height=height,
            )
            if png:
                return png, width, height
            if progress:
                progress(f"Three.js snapshot failed: {asset.virtual_path}")
            return None, None, None

        return render_model_preview_snapshot(bundle, width=width, height=height)
