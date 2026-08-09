"""Models Resource package export for logical Pokémon Generation V locations."""
from __future__ import annotations

import re
import hashlib
import shutil
import subprocess
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Callable
from urllib.parse import quote, unquote, urlparse, urlunparse

from ....core.assets import Asset
from ..exporter import convert_with_apicula
from ..gltf.compose import GlbScenePart, compose_glb_scenes
from ..map_objects import build_gen5_map_composition, resolve_gen5_map_objects
from .building_icons import (
    render_models_resource_building_icon,
    render_models_resource_building_preview_icon,
)
from .models_resource_map_catalog import MapCell, MapScene, MapSubmission

Progress = Callable[[str], None]
Snapshot = Callable[[Path], bytes | None]
_CELL_SIZE = 512.0


def _safe_title(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip(" .")
    return cleaned or "Unnamed Map"


def _scene_cell_positions(scene: MapScene) -> dict[int, tuple[float, float]]:
    """Map matrix X/Y to glTF X/Z; both axes advance in the positive direction."""
    return {
        cell.map_id: (
            cell.offset_x if cell.offset_x is not None else cell.x * _CELL_SIZE,
            cell.offset_z if cell.offset_z is not None else cell.y * _CELL_SIZE,
        )
        for cell in scene.cells
    }


def _bootstrap_terrain(
    rom_path: Path,
    cell: MapCell,
    rom_files: dict[str, bytes],
    output: Path,
) -> Path:
    objects = resolve_gen5_map_objects(
        rom_path, f"a/0/0/8/file_{cell.map_id:04d}.bin",
        map_index_override=cell.map_id, area_index_override=cell.area, _rom_files=rom_files,
    )
    model = Asset(
        asset_id=f"map-{cell.map_id}-bootstrap", virtual_path=f"map_{cell.map_id}.nsbmd",
        kind="Model", magic="BMD0", extension=".nsbmd", data=objects.terrain_data,
        original_data=objects.terrain_data, carved=True,
    )
    texture = Asset(
        asset_id=f"map-{cell.map_id}-texture", virtual_path=f"texture_{objects.area.map_texture}.nsbtx",
        kind="Texture", magic="BTX0", extension=".nsbtx", data=objects.map_texture_data,
        original_data=objects.map_texture_data,
    )
    siblings = [texture]
    if cell.additional_map_textures:
        from ..map_objects import _narc_files

        texture_packs = _narc_files(rom_files["a/0/1/4"], "a/0/1/4")
        siblings.extend(
            Asset(
                asset_id=f"map-{cell.map_id}-extra-texture-{index}",
                virtual_path=f"texture_{index}.nsbtx",
                kind="Texture",
                magic="BTX0",
                extension=".nsbtx",
                data=texture_packs[index],
                original_data=texture_packs[index],
            )
            for index in cell.additional_map_textures
        )
    result = convert_with_apicula(model, output, sibling_assets=siblings, output_format="glb", more_textures=False)
    glb = next((path for path in result.output_files if path.suffix.casefold() == ".glb"), None)
    if not result.ok or glb is None:
        raise RuntimeError(result.message or f"Could not convert map cell {cell.map_id}")
    return glb


def _export_cell(
    rom_path: Path,
    cell: MapCell,
    rom_files: dict[str, bytes],
    work: Path,
    progress: Progress | None,
) -> Path:
    cell_root = work / f"cell_{cell.matrix}_{cell.x}_{cell.y}_{cell.map_id}"
    bootstrap = _bootstrap_terrain(rom_path, cell, rom_files, cell_root / "bootstrap")
    source = bootstrap
    if cell.include_objects:
        composition = build_gen5_map_composition(
            rom_path, f"a/0/0/8/file_{cell.map_id:04d}.bin", bootstrap,
            cell_root / "composition", progress=progress, map_index_override=cell.map_id,
            area_index_override=cell.area,
            additional_map_texture_indices=cell.additional_map_textures,
            _rom_files=rom_files,
        )
        source = composition.composed_glb
    else:
        from ..gltf.embed_textures import embed_glb_external_images
        from ..gltf.glb_io import read_glb

        embedded = cell_root / "terrain_embedded.glb"
        embed_glb_external_images(
            read_glb(bootstrap),
            base_dir=bootstrap.parent,
            search_paths=[path for path in bootstrap.parent.iterdir() if path.is_file()],
            require_all=True,
        ).write(embedded)
        source = embedded
    if cell.remove_black_vertex_colors:
        source = _remove_all_black_vertex_colors(
            source,
            cell_root / "visible_vertex_lighting.glb",
        )
    if cell.max_component_center_x is not None:
        source = _prune_components_right_of(
            source,
            cell_root / "playable_component.glb",
            max_center_x=cell.max_component_center_x,
        )
    return source


def _remove_all_black_vertex_colors(source: Path, output: Path) -> Path:
    """Remove COLOR_0 only when every stored component is zero."""
    from ..gltf.glb_io import read_glb

    glb = read_glb(source)
    accessors = glb.json.get("accessors") or []
    views = glb.json.get("bufferViews") or []
    changed = False
    for mesh in glb.json.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            attributes = primitive.get("attributes") or {}
            accessor_index = attributes.get("COLOR_0")
            if not isinstance(accessor_index, int) or not 0 <= accessor_index < len(accessors):
                continue
            accessor = accessors[accessor_index]
            if accessor.get("componentType") != 5121 or accessor.get("type") not in {"VEC3", "VEC4"}:
                continue
            view_index = accessor.get("bufferView")
            if not isinstance(view_index, int) or not 0 <= view_index < len(views):
                continue
            width = 3 if accessor["type"] == "VEC3" else 4
            view = views[view_index]
            start = int(view.get("byteOffset", 0)) + int(accessor.get("byteOffset", 0))
            stride = int(view.get("byteStride", width))
            values = (
                glb.bin_chunk[start + row * stride : start + row * stride + width]
                for row in range(int(accessor.get("count", 0)))
            )
            if all(not any(value) for value in values):
                attributes.pop("COLOR_0", None)
                changed = True
    if not changed:
        return source
    output.parent.mkdir(parents=True, exist_ok=True)
    glb.write(output)
    return output


def _prune_components_right_of(source: Path, output: Path, *, max_center_x: float) -> Path:
    """Keep connected geometry belonging to the playable side of a map cell."""
    import trimesh

    from ..gltf import apply_platform_glb_policy

    scene = trimesh.load(source, force="scene", process=False)
    kept = trimesh.Scene()
    for node_index, node_name in enumerate(scene.graph.nodes_geometry):
        transform, geometry_name = scene.graph.get(node_name)
        geometry = scene.geometry[geometry_name]
        try:
            components = geometry.split(only_watertight=False)
        except (TypeError, ValueError):
            components = [geometry]
        for component_index, component in enumerate(components):
            bounds = trimesh.transform_points(component.bounds, transform)
            center_x = float(bounds[0][0] + bounds[1][0]) / 2.0
            if center_x <= max_center_x:
                kept.add_geometry(
                    component,
                    node_name=f"{node_name}_{node_index}_{component_index}",
                    geom_name=f"{geometry_name}_{node_index}_{component_index}",
                    transform=transform,
                )
    if not kept.geometry:
        raise RuntimeError("Playable-component filtering removed the entire map cell")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(kept.export(file_type="glb"))
    apply_platform_glb_policy(output)
    return output


def _compose_scene(
    rom_path: Path,
    submission: MapSubmission,
    scene_index: int,
    rom_files: dict[str, bytes],
    work: Path,
    progress: Progress | None,
) -> Path:
    scene = submission.scenes[scene_index]
    cell_positions = _scene_cell_positions(scene)
    min_x = min(position[0] for position in cell_positions.values())
    min_z = min(position[1] for position in cell_positions.values())
    parts: list[GlbScenePart] = []
    for position, cell in enumerate(scene.cells, 1):
        if progress:
            progress(f"{submission.title}: scene {scene_index + 1}, cell {position}/{len(scene.cells)}")
        source = _export_cell(rom_path, cell, rom_files, work, progress)
        cell_x, cell_z = cell_positions[cell.map_id]
        parts.append(GlbScenePart(
            source, f"map_{cell.map_id:04d}_{cell.x}_{cell.y}",
            translation=(cell_x - min_x, 0.0, cell_z - min_z),
        ))
    output = work / f"scene_{scene_index + 1:02d}.glb"
    return compose_glb_scenes(parts, output)


def _export_dae(glb: Path, output_dir: Path, stem: str) -> Path:
    from ..gltf.glb_io import read_glb
    from ..gltf.merge_animations import strip_all_animations

    assimp = shutil.which("assimp")
    if not assimp:
        raise RuntimeError("assimp is required for Models Resource DAE packages")
    output_dir.mkdir(parents=True, exist_ok=True)
    dae = output_dir / f"{stem}.dae"
    static_glb = output_dir / f".{stem}.static.glb"
    strip_all_animations(read_glb(glb)).write(static_glb)
    process = subprocess.run(
        [assimp, "export", str(static_glb), str(dae), "-f", "collada"],
        text=True, capture_output=True,
    )
    static_glb.unlink(missing_ok=True)
    if process.returncode or not dae.is_file():
        raise RuntimeError((process.stderr or process.stdout or "assimp DAE export failed").strip())
    text = dae.read_text(encoding="utf-8", errors="replace")
    if "<COLLADA" not in text or "<library_geometries" not in text:
        raise RuntimeError("assimp produced an invalid or empty COLLADA document")
    return dae


def _deduplicate_dae_textures(source: Path) -> None:
    """Remove byte-identical PNGs and redirect every COLLADA reference."""
    by_digest: dict[str, Path] = {}
    replacements: dict[str, str] = {}
    for texture in sorted(source.glob("*.png")):
        digest = hashlib.sha256(texture.read_bytes()).hexdigest()
        existing = by_digest.get(digest)
        if existing is None:
            by_digest[digest] = texture
        else:
            replacements[texture.name] = existing.name
            texture.unlink()
    if not replacements:
        return
    for dae in source.glob("*.dae"):
        text = dae.read_text(encoding="utf-8", errors="strict")
        def redirect(match: re.Match[str]) -> str:
            value = match.group(2).strip()
            parsed = urlparse(value)
            encoded_path = parsed.path if parsed.scheme else value
            decoded_path = unquote(encoded_path).replace("\\", "/")
            filename = decoded_path.rsplit("/", 1)[-1]
            canonical = replacements.get(filename)
            if canonical is None:
                return match.group(0)
            prefix = decoded_path[:-len(filename)] if filename else decoded_path
            redirected = quote(f"{prefix}{canonical}", safe="/:")
            if parsed.scheme:
                redirected = urlunparse(parsed._replace(path=redirected))
            whitespace_left = match.group(2)[:len(match.group(2)) - len(match.group(2).lstrip())]
            whitespace_right = match.group(2)[len(match.group(2).rstrip()):]
            return f"{match.group(1)}{whitespace_left}{redirected}{whitespace_right}{match.group(3)}"

        # Assimp emits percent escapes with lowercase hex digits on some
        # platforms (for example Pok%c3%a9mon), while urllib.quote uses uppercase.
        # Decode each COLLADA URL before matching instead of relying on a
        # case-sensitive textual replacement.
        text = re.sub(r"(<init_from>)(.*?)(</init_from>)", redirect, text, flags=re.DOTALL)
        dae.write_text(text, encoding="utf-8")


def _missing_dae_textures(source: Path) -> list[str]:
    """Return local COLLADA image references absent from the package folder."""
    missing: list[str] = []
    namespace = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    for dae in sorted(source.glob("*.dae")):
        try:
            root = ET.parse(dae)
        except ET.ParseError as exc:
            raise RuntimeError(f"Invalid COLLADA XML in {dae.name}: {exc}") from exc
        for node in root.findall(".//c:library_images/c:image/c:init_from", namespace):
            value = (node.text or "").strip()
            if not value:
                continue
            parsed = urlparse(value)
            if parsed.scheme and parsed.scheme != "file":
                continue
            decoded = unquote(parsed.path or value).replace("\\", "/")
            relative = Path(decoded)
            candidate = relative if relative.is_absolute() else source / relative
            if not candidate.is_file():
                missing.append(f"{dae.name}: {decoded}")
    return missing


def _zip_dae_folder(source: Path, package: Path) -> None:
    allowed = [path for path in source.iterdir() if path.is_file() and path.suffix.casefold() in {".dae", ".png"}]
    if not any(path.suffix.casefold() == ".dae" for path in allowed):
        raise RuntimeError("DAE package contains no model")
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(allowed):
            archive.write(path, path.name)
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("DAE package failed its ZIP CRC check")


def _verify_dae_folder(source: Path) -> None:
    missing = _missing_dae_textures(source)
    if missing:
        details = ", ".join(missing[:8])
        suffix = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        raise RuntimeError(f"DAE package has missing texture references: {details}{suffix}")
    assimp = shutil.which("assimp")
    if not assimp:
        raise RuntimeError("assimp is required for DAE verification")
    for dae in source.glob("*.dae"):
        result = subprocess.run([assimp, "info", str(dae)], text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or f"Could not re-import {dae.name}").strip())


def export_map_submission(
    rom_path: Path,
    output_root: Path,
    submission: MapSubmission,
    rom_files: dict[str, bytes],
    *,
    snapshot_renderer: Snapshot,
    progress: Progress | None = None,
    force: bool = False,
) -> Path:
    title = _safe_title(submission.title)
    destination = output_root / submission.section / "Locations" / title
    package = destination / f"{title}.zip"
    icon = destination / f"{title}_icon.png"
    preview_icon = destination / f"{title}_preview.png"
    preview_glb = destination / f"{title}_preview.glb"
    if not force and all(path.is_file() for path in (package, icon, preview_icon, preview_glb)):
        if progress:
            progress(f"Skipping complete package: {title}")
        return destination
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rae_black2_map_") as temp_name:
        work = Path(temp_name)
        scenes = [
            _compose_scene(rom_path, submission, index, rom_files, work / f"work_{index}", progress)
            for index in range(len(submission.scenes))
        ]
        dae_root = work / "dae"
        if submission.combine_scenes_in_dae and len(scenes) > 1:
            combined = compose_glb_scenes(
                [
                    GlbScenePart(scene, f"variant_{index:02d}_{submission.scenes[index - 1].name}")
                    for index, scene in enumerate(scenes, 1)
                ],
                work / "combined_variants.glb",
            )
            _export_dae(combined, dae_root, title)
        else:
            # Keep disconnected game scenes as separate DAE files instead of inventing a layout.
            for index, scene in enumerate(scenes, 1):
                scene_label = _safe_title(submission.scenes[index - 1].name)
                stem = title if len(scenes) == 1 else f"{title} - {scene_label}"
                _export_dae(scene, dae_root, stem)
        _deduplicate_dae_textures(dae_root)
        _verify_dae_folder(dae_root)
        _zip_dae_folder(dae_root, package)
        preferred_scene = {
            "Big Stadium": "Seating",
            "Small Court": "Seating",
        }.get(submission.title)
        primary = next(
            (
                scene_glb
                for scene, scene_glb in zip(submission.scenes, scenes)
                if scene.name == preferred_scene
            ),
            max(zip(submission.scenes, scenes), key=lambda pair: len(pair[0].cells))[1],
        )
        if submission.preview_yaw:
            primary = compose_glb_scenes(
                [GlbScenePart(primary, title, rotation_degrees=submission.preview_yaw)],
                work / "rotated_preview.glb",
            )
        shutil.copy2(primary, preview_glb)
    render_models_resource_building_icon(preview_glb, package, snapshot_renderer).save(icon, "PNG", optimize=True)
    render_models_resource_building_preview_icon(preview_glb, package, snapshot_renderer).save(
        preview_icon, "PNG", optimize=True,
    )
    return destination


def rerender_map_submission_images(
    output_root: Path,
    submission: MapSubmission,
    *,
    snapshot_renderer: Snapshot,
    progress: Progress | None = None,
) -> bool:
    """Replace only the two PNG renders for an already completed package."""
    title = _safe_title(submission.title)
    destination = output_root / submission.section / "Locations" / title
    package = destination / f"{title}.zip"
    preview_glb = destination / f"{title}_preview.glb"
    if not package.is_file() or not preview_glb.is_file():
        return False
    if progress:
        progress(f"Rerendering front-view images: {title}")
    render_models_resource_building_icon(preview_glb, package, snapshot_renderer).save(
        destination / f"{title}_icon.png", "PNG", optimize=True,
    )
    render_models_resource_building_preview_icon(preview_glb, package, snapshot_renderer).save(
        destination / f"{title}_preview.png", "PNG", optimize=True,
    )
    return True
