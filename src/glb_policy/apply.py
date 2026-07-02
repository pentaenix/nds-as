"""Apply shared material policy to apicula-produced GLB files."""
from __future__ import annotations

from pathlib import Path

from ..glb_policy.preview_textures import parse_glb_material_texture_map
from .classify import ClassificationResult, apply_render_class_to_material, classify_material
from .geometry_stats import compute_material_geometry_stats
from .glb_io import GlbData, read_glb


def _texture_bytes_for_material(glb_path: Path, texture_path: Path | None) -> bytes | None:
    if texture_path is None or not texture_path.is_file():
        return None
    try:
        return texture_path.read_bytes()
    except OSError:
        return None


def classify_glb_materials(glb_path: Path) -> dict[int, ClassificationResult]:
    glb = read_glb(glb_path)
    geom_stats = compute_material_geometry_stats(glb)
    texture_map = parse_glb_material_texture_map(glb_path)
    materials = glb.json.get("materials") or []
    out: dict[int, ClassificationResult] = {}
    for mat_index, material in enumerate(materials):
        if not isinstance(material, dict):
            continue
        texture_path = texture_map.get(str(mat_index))
        texture_bytes = _texture_bytes_for_material(glb_path, texture_path)
        out[mat_index] = classify_material(
            material,
            texture_bytes=texture_bytes,
            geometry_stats=geom_stats.get(mat_index),
        )
    return out


def apply_glb_policy(glb_path: Path, *, profile: str = "preview") -> dict[int, ClassificationResult]:
    """Rewrite a GLB in place with canonical material policy. Returns per-material results."""
    del profile  # reserved for future game-embed profile
    glb_path = Path(glb_path)
    glb = read_glb(glb_path)
    geom_stats = compute_material_geometry_stats(glb)
    texture_map = parse_glb_material_texture_map(glb_path)
    materials = glb.json.get("materials") or []
    results: dict[int, ClassificationResult] = {}

    for mat_index, material in enumerate(materials):
        if not isinstance(material, dict):
            continue
        texture_path = texture_map.get(str(mat_index))
        texture_bytes = _texture_bytes_for_material(glb_path, texture_path)
        result = classify_material(
            material,
            texture_bytes=texture_bytes,
            geometry_stats=geom_stats.get(mat_index),
        )
        apply_render_class_to_material(material, result)
        results[mat_index] = result

    glb.write(glb_path)
    return results
