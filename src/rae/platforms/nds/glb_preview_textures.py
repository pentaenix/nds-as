"""Deterministic GLB preview texture assignment for RAE.

apicula writes GLB files with external PNG URIs (``texture.png``) beside the model.
trimesh does not load those URIs, so RAE reads the glTF material table and attaches
the colocated PNGs before UV baking into vertex colors for pyqtgraph.
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

from ...core.texture_assignments import build_best_path_index, image_pixel_area, texture_key_for_path


@dataclass(frozen=True)
class MaterialPreviewState:
    alpha: float = 1.0
    alpha_mode: str = "OPAQUE"
    alpha_cutoff: float = 0.5
    double_sided: bool = False
    render_class: str = "opaque"


@dataclass(frozen=True)
class GlbMeshPart:
    """One preview/assigner part — typically one glTF primitive (material split)."""

    label: str
    material_index: int | None = None


def discover_colocated_textures(glb_path: Path) -> dict[str, Path]:
    """Index PNG/BMP/TGA files in the same folder as the GLB (apicula output)."""
    directory = glb_path.parent
    if not directory.is_dir():
        return {}
    paths: list[Path] = []
    for pattern in ("*.png", "*.PNG", "*.bmp", "*.BMP", "*.tga", "*.TGA"):
        paths.extend(p for p in directory.glob(pattern) if p.is_file())
    return build_best_path_index(paths)


def parse_glb_material_preview_states(glb_path: Path) -> dict[str, MaterialPreviewState]:
    """Map glTF material names (and index strings) to alpha/blend preview state."""
    try:
        gltf = read_glb_json(glb_path)
    except Exception:
        return {}

    out: dict[str, MaterialPreviewState] = {}
    for mat_idx, material in enumerate(gltf.get("materials") or []):
        if not isinstance(material, dict):
            continue
        pbr = material.get("pbrMetallicRoughness") or {}
        factor = pbr.get("baseColorFactor") or [1.0, 1.0, 1.0, 1.0]
        alpha = 1.0
        try:
            if isinstance(factor, (list, tuple)) and len(factor) >= 4:
                alpha = float(factor[3])
        except (TypeError, ValueError):
            alpha = 1.0
        alpha_mode = str(material.get("alphaMode") or "OPAQUE").upper()
        cutoff = 0.5
        try:
            if "alphaCutoff" in material:
                cutoff = float(material["alphaCutoff"])
        except (TypeError, ValueError):
            cutoff = 0.5
        double_sided = bool(material.get("doubleSided", False))
        extras = material.get("extras") or {}
        rae = extras.get("rae") or {}
        render_class = str(rae.get("renderClass") or "opaque").lower()
        state = MaterialPreviewState(
            alpha=alpha,
            alpha_mode=alpha_mode,
            alpha_cutoff=cutoff,
            double_sided=double_sided,
            render_class=render_class,
        )
        out[str(mat_idx)] = state
        name = str(material.get("name") or "").strip()
        if name:
            out[name.casefold()] = state
    return out


def parse_glb_material_texture_map(glb_path: Path) -> dict[str, Path]:
    """Map glTF material names (and index strings) to colocated PNG paths."""
    directory = glb_path.parent
    try:
        gltf = read_glb_json(glb_path)
    except Exception:
        return {}

    images = gltf.get("images") or []
    textures = gltf.get("textures") or []
    materials = gltf.get("materials") or []
    out: dict[str, Path] = {}

    for mat_idx, material in enumerate(materials):
        uri = _material_image_uri(material, textures, images)
        if not uri:
            continue
        path = directory / Path(uri).name
        if not path.is_file():
            continue
        out[str(mat_idx)] = path
        name = str(material.get("name") or "").strip()
        if name:
            out[name.casefold()] = path
        stem = Path(uri).stem.casefold()
        out[stem] = path
    return out


def ordered_texture_paths_from_glb(glb_path: Path, colocated: dict[str, Path]) -> list[Path]:
    """Return colocated PNG paths in glTF material order (stable for round-robin)."""
    material_map = parse_glb_material_texture_map(glb_path)
    if material_map:
        ordered: list[Path] = []
        seen: set[str] = set()
        try:
            gltf = read_glb_json(glb_path)
        except Exception:
            gltf = {}
        for mat_idx in range(len(gltf.get("materials") or [])):
            path = material_map.get(str(mat_idx))
            if path is None:
                continue
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            ordered.append(path)
        if ordered:
            return ordered
    return _unique_paths(list(colocated.values()))


def merge_texture_paths(*groups: list[Path]) -> list[Path]:
    """Dedupe path lists preserving order (first wins)."""
    out: list[Path] = []
    seen: set[str] = set()
    for group in groups:
        for path in group:
            try:
                key = str(path.resolve())
            except Exception:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
    return out


def merge_texture_by_name(*maps: dict[str, Path]) -> dict[str, Path]:
    """Merge name→path maps; later maps override earlier, preferring higher resolution."""
    out: dict[str, Path] = {}
    for mapping in maps:
        for key, path in mapping.items():
            key = key.casefold()
            current = out.get(key)
            if current is None or image_pixel_area(path) >= image_pixel_area(current):
                out[key] = path
    return out


def texture_map_from_paths(paths: list[Path]) -> dict[str, Path]:
    """Build a lookup table from PNG file stems (highest resolution per key)."""
    return build_best_path_index(paths)


def parse_glb_mesh_parts(glb_path: Path) -> list[GlbMeshPart]:
    """Return mesh-part labels from glTF primitives (matches trimesh scene.dump splits)."""
    try:
        gltf = read_glb_json(glb_path)
    except Exception:
        return []

    materials = gltf.get("materials") or []
    meshes = gltf.get("meshes") or []
    parts: list[GlbMeshPart] = []
    for mesh_idx, mesh in enumerate(meshes):
        if not isinstance(mesh, dict):
            continue
        mesh_name = str(mesh.get("name") or "").strip() or f"mesh_{mesh_idx}"
        primitives = mesh.get("primitives") or []
        if not primitives:
            parts.append(GlbMeshPart(mesh_name, None))
            continue
        for prim_idx, primitive in enumerate(primitives):
            if not isinstance(primitive, dict):
                continue
            raw_mat_idx = primitive.get("material")
            material_index = raw_mat_idx if isinstance(raw_mat_idx, int) else None
            label = mesh_name
            if material_index is not None and 0 <= material_index < len(materials):
                material = materials[material_index]
                if isinstance(material, dict):
                    mat_name = str(material.get("name") or "").strip()
                    if mat_name:
                        label = mat_name
            elif len(primitives) > 1:
                label = f"{mesh_name}_{prim_idx}"
            parts.append(GlbMeshPart(label, material_index))
    return parts


def parse_glb_mesh_part_labels(glb_path: Path) -> list[str]:
    return [part.label for part in parse_glb_mesh_parts(glb_path)]


def build_mesh_texture_paths_for_glb_parts(
    parts: list[GlbMeshPart],
    *,
    glb_path: Path | None = None,
    texture_by_name: dict[str, Path] | None = None,
    material_to_texture: dict[str, str] | None = None,
    texture_bind_order: list[str] | None = None,
    fallback_paths: list[Path] | None = None,
    mesh_texture_overrides: dict[str, Path] | None = None,
) -> list[Path | None]:
    """Return one PNG path per GLB mesh part (no trimesh required)."""
    texture_by_name = texture_by_name or {}
    material_to_texture = material_to_texture or {}
    texture_bind_order = texture_bind_order or []
    fallback_paths = fallback_paths or []
    mesh_texture_overrides = mesh_texture_overrides or {}

    colocated = discover_colocated_textures(glb_path) if glb_path is not None else {}
    glb_material_map = parse_glb_material_texture_map(glb_path) if glb_path is not None else {}
    merged_names = merge_texture_by_name(
        texture_map_from_paths(fallback_paths),
        texture_by_name,
        colocated,
        glb_material_map,
    )
    if glb_path is not None:
        unique_fallback = merge_texture_paths(
            ordered_texture_paths_from_glb(glb_path, colocated),
            fallback_paths,
        )
    else:
        unique_fallback = merge_texture_paths(list(colocated.values()), fallback_paths)

    out: list[Path | None] = []
    for mesh_index, part in enumerate(parts):
        override = mesh_texture_overrides.get(part.label)
        if override is not None and override.is_file():
            out.append(override)
            continue
        path = _path_for_glb_part(
            mesh_index,
            part.label,
            part.material_index,
            glb_path=glb_path,
            glb_material_map=glb_material_map,
            colocated=colocated,
            texture_by_name=merged_names,
            material_to_texture=material_to_texture,
            texture_bind_order=texture_bind_order,
            fallback_paths=unique_fallback,
        )
        out.append(path)
    return out


def build_mesh_texture_paths(
    named_meshes: list[tuple[str, object]],
    *,
    glb_path: Path | None = None,
    texture_by_name: dict[str, Path] | None = None,
    material_to_texture: dict[str, str] | None = None,
    texture_bind_order: list[str] | None = None,
    fallback_paths: list[Path] | None = None,
    mesh_texture_overrides: dict[str, Path] | None = None,
) -> list[Path | None]:
    """Return one PNG path per preview mesh (trimesh dump order)."""
    texture_by_name = texture_by_name or {}
    material_to_texture = material_to_texture or {}
    texture_bind_order = texture_bind_order or []
    fallback_paths = fallback_paths or []
    mesh_texture_overrides = mesh_texture_overrides or {}

    colocated = discover_colocated_textures(glb_path) if glb_path is not None else {}
    glb_material_map = parse_glb_material_texture_map(glb_path) if glb_path is not None else {}
    merged_names = merge_texture_by_name(
        texture_map_from_paths(fallback_paths),
        texture_by_name,
        colocated,
        glb_material_map,
    )
    if glb_path is not None:
        unique_fallback = merge_texture_paths(
            ordered_texture_paths_from_glb(glb_path, colocated),
            fallback_paths,
        )
    else:
        unique_fallback = merge_texture_paths(list(colocated.values()), fallback_paths)

    out: list[Path | None] = []
    for mesh_index, (geom_name, mesh) in enumerate(named_meshes):
        override = _mesh_texture_override(mesh_texture_overrides, geom_name, mesh)
        if override is not None and override.is_file():
            out.append(override)
            continue
        path = _path_for_mesh(
            mesh_index,
            geom_name,
            mesh,
            glb_path=glb_path,
            glb_material_map=glb_material_map,
            colocated=colocated,
            texture_by_name=merged_names,
            material_to_texture=material_to_texture,
            texture_bind_order=texture_bind_order,
            fallback_paths=unique_fallback,
        )
        out.append(path)
    return out


def attach_preview_textures(
    named_meshes: list[tuple[str, object]],
    mesh_texture_paths: list[Path | None],
) -> int:
    """Attach decoded PNGs to trimesh material.image for preview sampling."""
    attached = 0
    try:
        from PIL import Image as PILImage
    except Exception:
        return 0

    for (_geom_name, mesh), path in zip(named_meshes, mesh_texture_paths):
        if path is None or not path.is_file():
            continue
        visual = getattr(mesh, "visual", None)
        if visual is None:
            continue
        uv = getattr(visual, "uv", None)
        if uv is None:
            continue
        try:
            image = PILImage.open(path).convert("RGBA")
        except Exception:
            continue

        material = getattr(visual, "material", None)
        if material is None:
            try:
                import trimesh

                material = trimesh.visual.material.PBRMaterial(name=path.stem)
                visual.material = material
            except Exception:
                continue
        material.image = image
        attached += 1
    return attached


def apply_material_preview_alpha(colors, state: MaterialPreviewState | None):
    """Apply glTF material alpha / MASK cutoff to baked RGBA vertex colors."""
    if colors is None or state is None:
        return colors
    try:
        import numpy as np
    except Exception:
        return colors
    arr = np.asarray(colors, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 4 or len(arr) == 0:
        return colors
    out = arr.copy()
    out[:, 3] *= max(0.0, min(1.0, float(state.alpha)))
    if state.alpha_mode == "MASK":
        cutoff = max(0.0, min(1.0, float(state.alpha_cutoff)))
        out[out[:, 3] < cutoff, 3] = 0.0
    return out


def read_glb_json(glb_path: Path) -> dict:
    """Extract the JSON chunk from a binary GLB."""
    data = glb_path.read_bytes()
    if len(data) < 20:
        raise ValueError("GLB too small")
    magic, _version, _length = struct.unpack_from("<III", data, 0)
    if magic != 0x46546C67:
        raise ValueError("Not a GLB file")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != 0x4E4F534A:
        raise ValueError("GLB JSON chunk missing")
    json_bytes = data[20 : 20 + chunk_length]
    return json.loads(json_bytes.decode("utf-8"))


def _material_image_uri(material: dict, textures: list, images: list) -> str | None:
    pbr = material.get("pbrMetallicRoughness") or {}
    tex_info = pbr.get("baseColorTexture") or {}
    tex_idx = tex_info.get("index")
    if tex_idx is None:
        return None
    try:
        image_idx = textures[int(tex_idx)].get("source")
        if image_idx is None:
            return None
        uri = images[int(image_idx)].get("uri")
    except (IndexError, TypeError, ValueError):
        return None
    if not isinstance(uri, str) or not uri.strip():
        return None
    return uri.strip()


def _path_for_mesh(
    mesh_index: int,
    geom_name: str,
    mesh,
    *,
    glb_path: Path | None,
    glb_material_map: dict[str, Path],
    colocated: dict[str, Path],
    texture_by_name: dict[str, Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    fallback_paths: list[Path],
) -> Path | None:
    visual = getattr(mesh, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    mat_name = str(getattr(material, "name", "") or "").strip()
    mat_idx = _material_index(material, mesh)
    return _path_for_glb_part(
        mesh_index,
        mat_name or geom_name,
        mat_idx,
        glb_path=glb_path,
        glb_material_map=glb_material_map,
        colocated=colocated,
        texture_by_name=texture_by_name,
        material_to_texture=material_to_texture,
        texture_bind_order=texture_bind_order,
        fallback_paths=fallback_paths,
        extra_lookup_keys=_mesh_lookup_keys(geom_name, mesh),
    )


def _path_for_glb_part(
    mesh_index: int,
    label: str,
    material_index: int | None,
    *,
    glb_path: Path | None,
    glb_material_map: dict[str, Path],
    colocated: dict[str, Path],
    texture_by_name: dict[str, Path],
    material_to_texture: dict[str, str],
    texture_bind_order: list[str],
    fallback_paths: list[Path],
    extra_lookup_keys: list[str] | None = None,
) -> Path | None:
    mat_name = str(label or "").strip()
    mat_key = mat_name.casefold()

    # 1. glTF material table from apicula — authoritative, avoids index swaps.
    if mat_key and mat_key in glb_material_map:
        return glb_material_map[mat_key]

    if material_index is not None:
        path = glb_material_map.get(str(material_index))
        if path is not None:
            return path

    # 2. NSBMD material→texture binding (by material name, not mesh index).
    if mat_key:
        tex_name = material_to_texture.get(mat_key)
        if tex_name:
            path = _path_for_texture_name(tex_name, colocated=colocated, texture_by_name=texture_by_name, glb_path=glb_path)
            if path is not None:
                return path

    # 3. Material / geometry name direct lookup.
    lookup_keys = list(extra_lookup_keys or [])
    if mat_name and mat_name not in lookup_keys:
        lookup_keys.insert(0, mat_name)
    for key in lookup_keys:
        path = _path_for_key(
            key,
            colocated=colocated,
            texture_by_name=texture_by_name,
            material_to_texture=material_to_texture,
            glb_path=glb_path,
        )
        if path is not None:
            return path

    # 4. Round-robin only for unnamed meshes (mesh_0, mesh_1, …).
    if not mat_key and texture_bind_order:
        tex_key = texture_bind_order[mesh_index % len(texture_bind_order)].casefold()
        path = _path_for_texture_name(tex_key, colocated=colocated, texture_by_name=texture_by_name, glb_path=glb_path)
        if path is not None:
            return path

    if not mat_key and fallback_paths:
        return fallback_paths[mesh_index % len(fallback_paths)]
    return None


def _mesh_texture_override(
    overrides: dict[str, Path],
    geom_name: str,
    mesh,
) -> Path | None:
    if not overrides:
        return None
    for key in _mesh_override_lookup_keys(geom_name, mesh):
        path = overrides.get(key)
        if path is not None:
            return path
    return None


def _mesh_override_lookup_keys(geom_name: str, mesh) -> list[str]:
    keys: list[str] = []
    if geom_name:
        keys.append(geom_name)
    visual = getattr(mesh, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    if material is not None:
        mat_name = str(getattr(material, "name", "") or "").strip()
        if mat_name:
            keys.append(mat_name)
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        norm = key.strip()
        if not norm or norm in seen:
            continue
        seen.add(norm)
        out.append(norm)
    return out


def _material_index(material, mesh) -> int | None:
    if material is not None:
        for attr in ("index", "material_id", "mat_id"):
            value = getattr(material, attr, None)
            if isinstance(value, int):
                return value
        meta = getattr(material, "metadata", None)
        if isinstance(meta, dict):
            for key in ("index", "material_id", "mat_id"):
                value = meta.get(key)
                if isinstance(value, int):
                    return value
    meta = getattr(mesh, "metadata", None)
    if isinstance(meta, dict):
        for key in ("material_id", "mat_id", "material"):
            value = meta.get(key)
            if isinstance(value, int):
                return value
    return None


def _path_for_texture_name(
    tex_name: str,
    *,
    colocated: dict[str, Path],
    texture_by_name: dict[str, Path],
    glb_path: Path | None,
) -> Path | None:
    return _best_path_for_key(
        tex_name,
        colocated=colocated,
        texture_by_name=texture_by_name,
        glb_path=glb_path,
    )


def _path_from_uri(
    uri: str,
    *,
    glb_path: Path | None,
    colocated: dict[str, Path],
) -> Path | None:
    name = Path(uri.strip()).name
    stem = Path(name).stem.casefold()
    if stem in colocated:
        return colocated[stem]
    if glb_path is not None:
        candidate = glb_path.parent / name
        if candidate.is_file():
            return candidate
    return None


def _mesh_lookup_keys(geom_name: str, mesh) -> list[str]:
    keys: list[str] = []
    visual = getattr(mesh, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    if material is not None:
        for attr in ("name", "image_name", "file_name"):
            value = getattr(material, attr, None)
            if isinstance(value, str) and value.strip():
                keys.append(value)
    if geom_name and not geom_name.startswith("mesh_"):
        keys.append(geom_name)

    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        norm = Path(str(key)).stem.replace(".tga", "").strip("_").casefold()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(norm)
    return out


def _path_for_key(
    key: str,
    *,
    colocated: dict[str, Path],
    texture_by_name: dict[str, Path],
    material_to_texture: dict[str, str],
    glb_path: Path | None,
) -> Path | None:
    key = key.casefold().strip("_")
    if not key:
        return None

    tex_name = material_to_texture.get(key)
    if tex_name:
        path = _path_for_texture_name(tex_name, colocated=colocated, texture_by_name=texture_by_name, glb_path=glb_path)
        if path is not None:
            return path

    return _best_path_for_key(
        key,
        colocated=colocated,
        texture_by_name=texture_by_name,
        glb_path=glb_path,
    )


def _best_path_for_key(
    key: str,
    *,
    colocated: dict[str, Path],
    texture_by_name: dict[str, Path],
    glb_path: Path | None,
) -> Path | None:
    key = key.casefold().strip("_")
    if not key:
        return None
    candidates: list[Path] = []
    for mapping in (colocated, texture_by_name):
        for map_key, path in mapping.items():
            if map_key == key or map_key.startswith(f"{key}__") or texture_key_for_path(path) == key:
                candidates.append(path)
    if glb_path is not None:
        for suffix in (".png", ".PNG", ".bmp", ".BMP"):
            candidate = glb_path.parent / f"{key}{suffix}"
            if candidate.is_file():
                candidates.append(candidate)
    if not candidates:
        return None
    return max(candidates, key=image_pixel_area)


def _unique_paths(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            key = str(path.resolve())
        except Exception:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        out.append(path)
    return out
