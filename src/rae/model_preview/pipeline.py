"""Single model preview preparation path for viewport and EasyFind."""
from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..asset_resolver import MODEL_ANIMATION_MAGICS, folder_sibling_assets
from ..glb_policy.texture_patch import write_patched_preview_glb
from ..glb_preview_textures import (
    build_mesh_texture_paths_for_glb_parts,
    merge_texture_by_name,
    merge_texture_paths,
    parse_glb_mesh_parts,
    texture_map_from_paths,
)
from ..model_texture_resolver import (
    build_preview_texture_maps,
    resolve_model_textures,
    write_resolution_images,
)
from ..platforms.nds.exporter import convert_with_apicula, texture_outputs
from ..platforms.nds.scanner import Asset
from ..texture_library import TextureLibrary

Progress = Callable[[str], None]


@dataclass(frozen=True)
class ModelPreviewBundle:
    """Everything the viewport and thumbnail renderer need for one model."""

    source_glb: Path
    patched_glb: Path
    mesh_labels: tuple[str, ...]
    mesh_texture_paths: tuple[Path | None, ...]
    texture_by_name: dict[str, Path]
    material_to_texture: dict[str, str]
    texture_bind_order: tuple[str, ...]
    fallback_paths: tuple[Path, ...]


def prepare_model_preview(
    asset: Asset,
    all_assets: list[Asset],
    out_dir: Path,
    *,
    texture_library: TextureLibrary | None = None,
    progress: Progress | None = None,
) -> ModelPreviewBundle | None:
    """Resolve textures, convert, patch GLB — identical to the viewport web path."""
    if asset.magic.upper() != "BMD0" or not asset.data:
        return None

    def log(text: str) -> None:
        if progress:
            progress(text)

    library = texture_library
    if library is None:
        log("Building texture dictionary for model preview…")
        library = TextureLibrary.from_assets(all_assets, progress=progress)

    resolution = resolve_model_textures(
        asset,
        all_assets,
        texture_library=library,
        progress=progress,
    )

    siblings = [
        item
        for item in folder_sibling_assets(
            asset,
            all_assets,
            allowed_magics=MODEL_ANIMATION_MAGICS | {"BTX0"},
            limit=16,
        )
        if item.asset_id != asset.asset_id
    ]
    for item in resolution.resolved_assets:
        if item.magic == "BTX0" and item.asset_id not in {s.asset_id for s in siblings}:
            siblings.append(item)

    out_dir.mkdir(parents=True, exist_ok=True)
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    result = convert_with_apicula(
        asset,
        out_dir,
        sibling_assets=siblings[:16],
        output_format="glb",
        more_textures=True,
    )
    if not result.ok or not result.output_files:
        return None

    source_glb = next(
        (path for path in result.output_files if path.suffix.lower() == ".glb" and path.is_file()),
        None,
    )
    if source_glb is None:
        return None

    apicula_pngs = texture_outputs(out_dir)
    aux = write_resolution_images(resolution, out_dir / "dsm_resolved_textures")
    all_pngs = merge_texture_paths(apicula_pngs, aux)
    resolver_map, material_to_texture, bind_order = build_preview_texture_maps(resolution, aux)
    apicula_map = texture_map_from_paths(apicula_pngs)
    texture_by_name = merge_texture_by_name(resolver_map, apicula_map)
    fallback_paths = tuple(all_pngs)

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
    )
