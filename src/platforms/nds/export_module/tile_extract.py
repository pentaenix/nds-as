"""Export selected preview materials as an isolated Pokemon Resort tile."""
from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ....core.assets import Asset
from ..gltf.embed_textures import embed_glb_external_images
from ..gltf.extract import extract_material_primitives
from ..gltf.glb_io import read_glb
from ..model_module.preview_pipeline import ModelPreviewBundle
from .tile_bundle import (
    _material_animations,
    _material_motion_animations,
    _merge_animation_sources,
    render_tile_preview_png,
    write_tile_archive,
)


@dataclass(frozen=True)
class PreviewTileBatchItem:
    """One exact spatial occurrence written by Tile Extractor batch export."""

    name: str
    filename: str
    selected_materials: tuple[str, ...]
    spatial_tile_bounds: tuple[float, float, float, float]
    footprint: tuple[int, int]
    origin_y: float | None = None
    rotation_quarter_turns: int = 0


def _rotate_tile_scene(model_glb: Path, quarter_turns: int) -> None:
    """Rotate a centered tile in 90-degree steps without rewriting geometry."""
    turns = int(quarter_turns) % 4
    if turns == 0:
        return
    glb = read_glb(model_glb)
    scenes = glb.json.setdefault("scenes", [{"nodes": []}])
    scene_index = int(glb.json.get("scene") or 0)
    if not (0 <= scene_index < len(scenes)):
        scene_index = 0
        glb.json["scene"] = 0
    roots = [
        int(value)
        for value in (scenes[scene_index].get("nodes") or [])
        if isinstance(value, int)
    ]
    angle = turns * math.pi / 2.0
    nodes = glb.json.setdefault("nodes", [])
    nodes.append({
        "name": f"rae_tile_rotation_{turns * 90}",
        "rotation": [0.0, math.sin(angle / 2.0), 0.0, math.cos(angle / 2.0)],
        "children": roots,
    })
    scenes[scene_index]["nodes"] = [len(nodes) - 1]
    glb.json.setdefault("extras", {}).setdefault("rae", {})["tileRotationDegrees"] = turns * 90
    glb.write(model_glb)


def _isolated_preview_bundle(
    *,
    source_glb: Path,
    selected_materials: list[str],
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    temp: Path,
    component_indices: dict[str, int] | None = None,
    repeat_patch_materials: set[str] | None = None,
    spatial_tile_bounds: tuple[float, float, float, float] | None = None,
    preserve_spatial_components: bool = False,
    spatial_component_center_filter: bool = False,
    origin_y: float | None = None,
    rotation_quarter_turns: int = 0,
) -> ModelPreviewBundle:
    """Build the exact self-contained GLB used by extractor preview/export."""
    isolated = extract_material_primitives(
        source_glb,
        temp / "isolated.glb",
        selected_materials,
        recenter=True,
        component_indices=component_indices,
        repeat_patch_materials=repeat_patch_materials,
        spatial_tile_bounds=spatial_tile_bounds,
        preserve_spatial_components=preserve_spatial_components,
        spatial_component_center_filter=spatial_component_center_filter,
        origin_y=origin_y,
    )
    embedded = embed_glb_external_images(
        read_glb(isolated),
        base_dir=source_glb.parent,
        search_paths=[*fallback_paths, *texture_by_name.values()],
        require_all=True,
    )
    model_glb = temp / "model.glb"
    embedded.write(model_glb)
    _rotate_tile_scene(model_glb, rotation_quarter_turns)
    return ModelPreviewBundle(
        source_glb=model_glb,
        patched_glb=model_glb,
        mesh_labels=tuple(mesh_labels),
        mesh_texture_paths=tuple(mesh_texture_paths),
        texture_by_name=dict(texture_by_name),
        material_to_texture=dict(material_to_texture),
        texture_bind_order=tuple(texture_bind_order),
        fallback_paths=tuple(fallback_paths),
    )


def render_preview_materials_png(
    *,
    source_glb: Path,
    selected_materials: list[str],
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    width: int = 480,
    height: int = 240,
    yaw_deg: float = 35.0,
    pitch_deg: float = 28.0,
    component_indices: dict[str, int] | None = None,
    repeat_patch_materials: set[str] | None = None,
    spatial_tile_bounds: tuple[float, float, float, float] | None = None,
    preserve_spatial_components: bool = False,
    spatial_component_center_filter: bool = False,
    origin_y: float | None = None,
) -> bytes | None:
    """Render only what Tile Extractor will put in the downloaded model."""
    from ....model_preview.scene_snapshot import render_model_preview_snapshot

    with tempfile.TemporaryDirectory(prefix="rae_nds_tile_preview_") as temp_name:
        bundle = _isolated_preview_bundle(
            source_glb=source_glb,
            selected_materials=selected_materials,
            mesh_labels=mesh_labels,
            mesh_texture_paths=mesh_texture_paths,
            texture_by_name=texture_by_name,
            fallback_paths=fallback_paths,
            material_to_texture=material_to_texture,
            texture_bind_order=texture_bind_order,
            temp=Path(temp_name),
            component_indices=component_indices,
            repeat_patch_materials=repeat_patch_materials,
            spatial_tile_bounds=spatial_tile_bounds,
            preserve_spatial_components=preserve_spatial_components,
            spatial_component_center_filter=spatial_component_center_filter,
            origin_y=origin_y,
        )
        png, _, _ = render_model_preview_snapshot(
            bundle,
            width=width,
            height=height,
            yaw_deg=yaw_deg,
            pitch_deg=pitch_deg,
        )
        return png


def build_preview_materials_glb(
    *,
    source_glb: Path,
    selected_materials: list[str],
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    out_dir: Path,
    component_indices: dict[str, int] | None = None,
    repeat_patch_materials: set[str] | None = None,
    spatial_tile_bounds: tuple[float, float, float, float] | None = None,
    preserve_spatial_components: bool = False,
    spatial_component_center_filter: bool = False,
    origin_y: float | None = None,
) -> ModelPreviewBundle:
    """Build a persistent, centered, self-contained GLB for 3D tile preview."""
    out_dir.mkdir(parents=True, exist_ok=True)
    return _isolated_preview_bundle(
        source_glb=source_glb,
        selected_materials=selected_materials,
        mesh_labels=mesh_labels,
        mesh_texture_paths=mesh_texture_paths,
        texture_by_name=texture_by_name,
        fallback_paths=fallback_paths,
        material_to_texture=material_to_texture,
        texture_bind_order=texture_bind_order,
        temp=out_dir,
        component_indices=component_indices,
        repeat_patch_materials=repeat_patch_materials,
        spatial_tile_bounds=spatial_tile_bounds,
        preserve_spatial_components=preserve_spatial_components,
        spatial_component_center_filter=spatial_component_center_filter,
        origin_y=origin_y,
    )


def export_preview_materials_as_tile(
    *,
    asset: Asset,
    source_glb: Path,
    selected_materials: list[str],
    output_path: Path,
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    material_specs: dict[str, dict],
    component_indices: dict[str, int] | None = None,
    repeat_patch_materials: set[str] | None = None,
    spatial_tile_bounds: tuple[float, float, float, float] | None = None,
    preserve_spatial_components: bool = False,
    spatial_component_center_filter: bool = False,
    origin_y: float | None = None,
) -> Path:
    """Package only the selected parts using the user's current preview bindings."""
    selected_keys = {name.casefold() for name in selected_materials}
    selected_specs = {
        name: spec
        for name, spec in material_specs.items()
        if name.casefold() in selected_keys
    }
    with tempfile.TemporaryDirectory(prefix="rae_nds_tile_extract_") as temp_name:
        temp = Path(temp_name)
        preview_bundle = _isolated_preview_bundle(
            source_glb=source_glb,
            selected_materials=selected_materials,
            mesh_labels=mesh_labels,
            mesh_texture_paths=mesh_texture_paths,
            texture_by_name=texture_by_name,
            fallback_paths=fallback_paths,
            material_to_texture=material_to_texture,
            texture_bind_order=texture_bind_order,
            temp=temp,
            component_indices=component_indices,
            repeat_patch_materials=repeat_patch_materials,
            spatial_tile_bounds=spatial_tile_bounds,
            preserve_spatial_components=preserve_spatial_components,
            spatial_component_center_filter=spatial_component_center_filter,
            origin_y=origin_y,
        )
        model_glb = preview_bundle.patched_glb
        staging = temp / "bundle"
        staging.mkdir()
        animations = _merge_animation_sources(
            _material_animations(selected_specs, preview_bundle, staging),
            _material_motion_animations(model_glb, staging),
        )
        preview_png = render_tile_preview_png(preview_bundle)
        return write_tile_archive(
            output_path,
            model_glb=model_glb,
            asset=asset,
            animations=animations,
            staging=staging,
            selected_materials=selected_materials,
            preview_png=preview_png,
        )


def export_preview_material_occurrences_as_tiles(
    *,
    asset: Asset,
    source_glb: Path,
    items: list[PreviewTileBatchItem],
    output_dir: Path,
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    material_specs: dict[str, dict],
    progress: Callable[[int, int, Path], None] | None = None,
) -> list[Path]:
    """Export every inferred occurrence in one extractor row as a `.tile`.

    Motion frames are baked once per unique material stack and reused while
    writing the independent archives. Each archive remains portable and can be
    imported by itself in Pokemon Resort Admin.
    """
    if not items:
        return []
    output_dir.mkdir(parents=True, exist_ok=True)
    filenames = [Path(item.filename).name for item in items]
    if len(set(name.casefold() for name in filenames)) != len(filenames):
        raise ValueError("Batch tile filenames must be unique.")

    written: list[Path] = []
    with tempfile.TemporaryDirectory(prefix="rae_nds_tile_batch_") as temp_name:
        temp = Path(temp_name)
        animation_cache: dict[tuple[str, ...], tuple[list[dict], Path]] = {}
        for index, item in enumerate(items, start=1):
            selected = list(item.selected_materials)
            if not selected:
                continue
            item_temp = temp / f"occurrence_{index:04d}"
            item_temp.mkdir(parents=True, exist_ok=True)
            preview_bundle = _isolated_preview_bundle(
                source_glb=source_glb,
                selected_materials=selected,
                mesh_labels=mesh_labels,
                mesh_texture_paths=mesh_texture_paths,
                texture_by_name=texture_by_name,
                fallback_paths=fallback_paths,
                material_to_texture=material_to_texture,
                texture_bind_order=texture_bind_order,
                temp=item_temp,
                spatial_tile_bounds=item.spatial_tile_bounds,
                origin_y=item.origin_y,
                rotation_quarter_turns=item.rotation_quarter_turns,
            )
            model_glb = preview_bundle.patched_glb
            material_key = tuple(material.casefold() for material in selected)
            cached = animation_cache.get(material_key)
            if cached is None:
                staging = temp / f"animations_{len(animation_cache):03d}"
                staging.mkdir(parents=True, exist_ok=True)
                selected_keys = set(material_key)
                selected_specs = {
                    name: spec
                    for name, spec in material_specs.items()
                    if name.casefold() in selected_keys
                }
                animations = _merge_animation_sources(
                    _material_animations(selected_specs, preview_bundle, staging),
                    _material_motion_animations(model_glb, staging),
                )
                animation_cache[material_key] = (animations, staging)
            else:
                animations, staging = cached

            output = output_dir / Path(item.filename).name
            preview_png = render_tile_preview_png(preview_bundle)
            written.append(
                write_tile_archive(
                    output,
                    model_glb=model_glb,
                    asset=asset,
                    animations=animations,
                    staging=staging,
                    selected_materials=selected,
                    name_override=item.name,
                    source_details={
                        "batchOccurrence": index,
                        "batchCount": len(items),
                        "spatialBounds": list(item.spatial_tile_bounds),
                        "footprint": {"width": item.footprint[0], "height": item.footprint[1]},
                    },
                    preview_png=preview_png,
                    footprint=item.footprint,
                )
            )
            if progress is not None:
                progress(index, len(items), output)
    return written
