"""NDS BMD0 → textured GLB preview pipeline (apicula + texture resolver)."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ....core.assets import Asset
from ....core.util import sanitize_virtual_path
from ..gltf.preview_textures import (
    build_mesh_texture_paths_for_glb_parts,
    parse_glb_mesh_parts,
    prefer_resolver_texture_map,
    texture_map_from_paths,
)
from ..gltf.texture_patch import write_patched_preview_glb
from ....preview_policy import ModelPreviewPolicy, ModelPreviewQuality, model_preview_policy
from ..asset_resolver import MODEL_ANIMATION_MAGICS, folder_sibling_assets
from ..exporter import convert_with_apicula, texture_outputs
from ..model_texture_resolver import (
    ModelTextureResolution,
    build_preview_texture_maps,
    resolve_model_textures,
    write_resolution_images,
)
from ..texture_library import TextureLibrary

Progress = Callable[[str], None]


@dataclass(frozen=True)
class ModelPreviewBundle:
    """Everything the viewport and thumbnail renderer need for one NDS model."""

    source_glb: Path
    patched_glb: Path
    mesh_labels: tuple[str, ...]
    mesh_texture_paths: tuple[Path | None, ...]
    texture_by_name: dict[str, Path]
    material_to_texture: dict[str, str]
    texture_bind_order: tuple[str, ...]
    fallback_paths: tuple[Path, ...]


def _resolve_textures_for_bundle(
    asset: Asset,
    all_assets: list[Asset],
    *,
    texture_library: TextureLibrary | None,
    manual_texture: Asset | None,
    policy: ModelPreviewPolicy,
    progress: Progress | None,
) -> ModelTextureResolution:
    """Match TextureResolveWorker: fast path first, then ROM texture dictionary."""
    resolution = resolve_model_textures(
        asset,
        all_assets,
        texture_library=None,
        manual_texture=manual_texture,
        defer_library_build=True,
        progress=progress,
        policy=policy,
    )
    if resolution.verified or (resolution.decoded_images and resolution.status != "unresolved"):
        return resolution

    library = texture_library
    if library is None:
        if progress:
            progress("Building texture dictionary for model conversion…")
        library = TextureLibrary.from_assets(all_assets, progress=progress)

    return resolve_model_textures(
        asset,
        all_assets,
        texture_library=library,
        manual_texture=manual_texture,
        progress=progress,
        policy=policy,
    )


def _conversion_siblings(
    asset: Asset,
    all_assets: list[Asset],
    resolution: ModelTextureResolution,
) -> list[Asset]:
    """Resolved texture archives plus same-folder animation siblings only.

    Do not pass every NSBTX in battle-background folders (``a/0/1/1``) to apicula:
    duplicate dictionary names across unrelated archives cause wrong textures.
    """
    siblings: list[Asset] = []
    seen = {asset.asset_id}

    def add(item: Asset) -> None:
        if item.asset_id in seen:
            return
        seen.add(item.asset_id)
        siblings.append(item)

    for item in resolution.resolved_assets:
        if item.magic == "BTX0":
            add(item)
    for item in folder_sibling_assets(
        asset,
        all_assets,
        allowed_magics=MODEL_ANIMATION_MAGICS,
        limit=16,
    ):
        add(item)
    return siblings[:16]


def build_textured_model_bundle(
    asset: Asset,
    all_assets: list[Asset],
    out_dir: Path,
    *,
    texture_library: TextureLibrary | None = None,
    manual_texture: Asset | None = None,
    policy: ModelPreviewPolicy | None = None,
    cap_for_viewport: bool = True,
    progress: Progress | None = None,
) -> tuple[ModelPreviewBundle | None, str]:
    """Resolve textures, convert with apicula, patch GLB — shared by preview and export."""
    if asset.magic.upper() != "BMD0" or not asset.data:
        return None, "Selected asset is not a BMD0 model."

    policy = model_preview_policy(policy)
    if policy.geometry_only:
        policy = model_preview_policy(ModelPreviewQuality.BALANCED)

    resolution = _resolve_textures_for_bundle(
        asset,
        all_assets,
        texture_library=texture_library,
        manual_texture=manual_texture,
        policy=policy,
        progress=progress,
    )
    siblings = _conversion_siblings(asset, all_assets, resolution)

    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = convert_with_apicula(
        asset,
        out_dir,
        sibling_assets=siblings,
        output_format="glb",
        more_textures=policy.full_fidelity,
    )
    if not result.ok:
        return None, result.message or "apicula convert failed"
    if not result.output_files:
        return None, result.message or "apicula completed without GLB output"

    source_glb = next(
        (path for path in result.output_files if path.suffix.lower() == ".glb" and path.is_file()),
        None,
    )
    if source_glb is None:
        return None, "apicula output did not include a .glb file"

    apicula_pngs = texture_outputs(out_dir)
    aux = write_resolution_images(
        resolution,
        out_dir / "dsm_resolved_textures",
        max_images=policy.max_decoded_images,
    )

    if cap_for_viewport and not policy.full_fidelity:
        viewport_texture_cap = min(policy.max_decoded_images or 512, 128)
    else:
        viewport_texture_cap = policy.max_decoded_images

    if aux:
        viewport_pngs = list(aux if viewport_texture_cap is None else aux[:viewport_texture_cap])
    else:
        viewport_pngs = list(
            apicula_pngs if viewport_texture_cap is None else apicula_pngs[:viewport_texture_cap]
        )
    capped_apicula_pngs = list(apicula_pngs[:64])
    resolver_map, material_to_texture, bind_order = build_preview_texture_maps(
        resolution,
        list(aux if viewport_texture_cap is None else aux[:viewport_texture_cap]) if aux else [],
    )
    apicula_map = texture_map_from_paths(capped_apicula_pngs)
    texture_by_name = prefer_resolver_texture_map(resolver_map, apicula_map)
    fallback_paths = tuple(viewport_pngs)

    parts = parse_glb_mesh_parts(source_glb)
    mesh_labels = tuple(part.label for part in parts)
    mesh_texture_paths = tuple(
        build_mesh_texture_paths_for_glb_parts(
            parts,
            glb_path=source_glb,
            texture_by_name=texture_by_name,
            material_to_texture=material_to_texture,
            texture_bind_order=bind_order,
            fallback_paths=list(fallback_paths),
            prefer_material_bindings=bool(resolution.bindings),
        )
    )

    patched_glb = out_dir / "rae_preview.glb"
    write_patched_preview_glb(
        source_glb,
        patched_glb,
        mesh_labels=list(mesh_labels),
        mesh_texture_paths=list(mesh_texture_paths),
        texture_by_name=texture_by_name,
        material_to_texture=material_to_texture,
        stage_texture_paths=list(fallback_paths),
    )

    return ModelPreviewBundle(
        source_glb=source_glb,
        patched_glb=patched_glb,
        mesh_labels=mesh_labels,
        mesh_texture_paths=mesh_texture_paths,
        texture_by_name=texture_by_name,
        material_to_texture=material_to_texture,
        texture_bind_order=tuple(bind_order),
        fallback_paths=fallback_paths,
    ), ""


def prepare_model_preview(
    asset: Asset,
    all_assets: list[Asset],
    out_dir: Path,
    *,
    texture_library: TextureLibrary | None = None,
    progress: Progress | None = None,
) -> ModelPreviewBundle | None:
    """Resolve textures, convert, patch GLB — identical to the viewport web path."""
    bundle, _error = build_textured_model_bundle(
        asset,
        all_assets,
        out_dir,
        texture_library=texture_library,
        policy=model_preview_policy(ModelPreviewQuality.BALANCED),
        cap_for_viewport=True,
        progress=progress,
    )
    return bundle


def finalize_textured_glb_export(
    bundle: ModelPreviewBundle,
    out_dir: Path,
    *,
    glb_filename: str,
) -> list[Path]:
    """Write one self-contained GLB with embedded textures (animations preserved)."""
    from ....core.util import sanitize_virtual_path
    from ..gltf.embed_textures import embed_glb_external_images
    from ..gltf.glb_io import read_glb
    from ..gltf.merge_animations import merge_glb_animations
    from ..gltf.platform_animation import freeze_horizontal_platform_joints

    out_dir.mkdir(parents=True, exist_ok=True)
    final_glb = out_dir / glb_filename

    search_paths = [bundle.patched_glb.parent, bundle.source_glb.parent]
    search_paths.extend(Path(path).parent for path in bundle.texture_by_name.values())
    search_paths.extend(bundle.fallback_paths)

    glb = read_glb(bundle.patched_glb)
    embedded = embed_glb_external_images(
        glb,
        base_dir=bundle.patched_glb.parent,
        search_paths=search_paths,
        require_all=True,
    )
    anim_name = Path(glb_filename).stem or "Animation"
    merged = merge_glb_animations(embedded, name=anim_name)
    merged = freeze_horizontal_platform_joints(merged)
    merged.write(final_glb)
    return [final_glb]


def export_textured_model_glb(
    asset: Asset,
    all_assets: list[Asset],
    out_dir: Path,
    *,
    texture_library: TextureLibrary | None = None,
    manual_texture: Asset | None = None,
    policy: ModelPreviewPolicy | None = None,
    progress: Progress | None = None,
    glb_filename: str | None = None,
) -> list[Path]:
    """Export a self-contained GLB: embedded textures, single file, animations kept."""
    policy = model_preview_policy(policy)
    if policy.geometry_only:
        policy = model_preview_policy(ModelPreviewQuality.FULL_FIDELITY)

    work_dir = out_dir / "_rae_export_work"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)

    bundle, error = build_textured_model_bundle(
        asset,
        all_assets,
        work_dir,
        texture_library=texture_library,
        manual_texture=manual_texture,
        policy=policy,
        cap_for_viewport=False,
        progress=progress,
    )
    if bundle is None:
        detail = error.strip() if error else "apicula did not produce a GLB"
        raise RuntimeError(f"Model conversion failed — {detail}")

    stem = sanitize_virtual_path(asset.virtual_path).stem or asset.asset_id
    filename = glb_filename or f"{stem}.glb"
    written = finalize_textured_glb_export(bundle, out_dir, glb_filename=filename)
    shutil.rmtree(work_dir, ignore_errors=True)
    return written
