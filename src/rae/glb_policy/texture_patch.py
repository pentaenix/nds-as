"""Patch glTF material image URIs inside a GLB for preview/export."""
from __future__ import annotations

import copy
import shutil
from pathlib import Path

from .classify import (
    ClassificationResult,
    RenderClass,
    _material_alpha,
    apply_render_class_to_material,
    classify_material,
)
from .texture_alpha import texture_has_partial_alpha_channel
from .geometry_stats import compute_material_geometry_stats
from .glb_io import GlbData, read_glb


def prebake_texture_alpha(source: Path, dest: Path, *, material_alpha: float) -> Path:
    """Bake uniform material alpha into PNG alpha (matches legacy GL preview shader)."""
    source = source.resolve()
    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)
    alpha = max(0.0, min(1.0, float(material_alpha)))
    if alpha >= 0.999:
        if source != dest:
            shutil.copy2(source, dest)
        return dest
    from PIL import Image

    img = Image.open(source).convert("RGBA")
    red, green, blue, alpha_band = img.split()
    alpha_band = alpha_band.point(lambda value: int(value * alpha))
    Image.merge("RGBA", (red, green, blue, alpha_band)).save(dest)
    return dest


def _materials_by_name(gltf: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for material in gltf.get("materials") or []:
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or "").strip().casefold()
        if name:
            out[name] = material
    return out


def _source_render_classes(glb: GlbData) -> dict[str, str]:
    classes: dict[str, str] = {}
    for material in glb.json.get("materials") or []:
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or "").strip().casefold()
        if not name:
            continue
        render_class = str(((material.get("extras") or {}).get("rae") or {}).get("renderClass") or "").strip().lower()
        if render_class:
            classes[name] = render_class
    return classes


def _reset_material_opacity(material: dict) -> None:
    pbr = material.setdefault("pbrMetallicRoughness", {})
    if isinstance(pbr, dict):
        factor = list(pbr.get("baseColorFactor") or [1.0, 1.0, 1.0, 1.0])
        while len(factor) < 4:
            factor.append(1.0)
        factor[3] = 1.0
        pbr["baseColorFactor"] = factor
    extras = dict(material.get("extras") or {})
    rae = dict(extras.get("rae") or {})
    nitro = dict(rae.get("nitro") or {})
    nitro["alpha"] = 1.0
    rae["nitro"] = nitro
    extras["rae"] = rae
    material["extras"] = extras


def ensure_baked_preview_texture(
    source_glb: Path,
    material_name: str,
    png_path: Path,
    *,
    preview_dir: Path | None = None,
) -> Path:
    """Return a preview PNG with material alpha prebaked for WebEngine / flipbook use."""
    source_glb = source_glb.resolve()
    png_path = png_path.resolve()
    preview_dir = (preview_dir or source_glb.parent / ".rae_preview" / "baked").resolve()
    preview_dir.mkdir(parents=True, exist_ok=True)
    mat_key = str(material_name or "").strip().casefold()
    baked_path = preview_dir / f"{mat_key}__{png_path.name}"
    glb = read_glb(source_glb)
    material = _materials_by_name(glb.json).get(mat_key)
    material_alpha = _material_alpha(material) if material else 1.0
    if baked_path.is_file():
        try:
            if baked_path.stat().st_mtime >= png_path.stat().st_mtime:
                return baked_path
        except OSError:
            pass
    return prebake_texture_alpha(png_path, baked_path, material_alpha=material_alpha)


def ensure_colocated_texture(
    png_path: Path,
    glb_directory: Path,
    *,
    material: dict | None = None,
) -> str:
    """Copy or prebake PNG beside the GLB; return basename for the image URI."""
    png_path = png_path.resolve()
    glb_directory = glb_directory.resolve()
    material_alpha = _material_alpha(material) if material else 1.0
    if material_alpha < 0.999:
        dest = glb_directory / f"baked_{png_path.stem}{png_path.suffix.lower()}"
        prebake_texture_alpha(png_path, dest, material_alpha=material_alpha)
        return dest.name
    if png_path.parent == glb_directory:
        return png_path.name
    dest = glb_directory / png_path.name
    if not dest.is_file() or dest.read_bytes() != png_path.read_bytes():
        shutil.copy2(png_path, dest)
    return dest.name


def patch_glb_material_textures(
    glb: GlbData,
    material_images: dict[str, str],
) -> GlbData:
    """Return a new GlbData with material baseColor image URIs rewritten."""
    if not material_images:
        return glb

    gltf = copy.deepcopy(glb.json)
    materials = list(gltf.get("materials") or [])
    images = list(gltf.get("images") or [])
    textures = list(gltf.get("textures") or [])

    name_to_index: dict[str, int] = {}
    for mat_idx, material in enumerate(materials):
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or "").strip()
        if name:
            name_to_index[name.casefold()] = mat_idx

    for mat_name, filename in material_images.items():
        key = str(mat_name or "").strip().casefold()
        filename = str(filename or "").strip()
        if not key or not filename:
            continue
        mat_idx = name_to_index.get(key)
        if mat_idx is None or mat_idx >= len(materials):
            continue
        material = materials[mat_idx]
        if not isinstance(material, dict):
            continue
        pbr = material.setdefault("pbrMetallicRoughness", {})
        if not isinstance(pbr, dict):
            continue

        tex_idx = None
        base_tex = pbr.get("baseColorTexture")
        if isinstance(base_tex, dict):
            raw = base_tex.get("index")
            if isinstance(raw, int):
                tex_idx = raw

        if isinstance(tex_idx, int) and 0 <= tex_idx < len(textures):
            texture = textures[tex_idx]
            if isinstance(texture, dict):
                src_idx = texture.get("source")
                if isinstance(src_idx, int) and 0 <= src_idx < len(images):
                    image = dict(images[src_idx]) if isinstance(images[src_idx], dict) else {}
                    image["uri"] = filename
                    images[src_idx] = image
                    continue

        images.append({"uri": filename})
        textures.append({"source": len(images) - 1})
        pbr["baseColorTexture"] = {"index": len(textures) - 1}

    gltf["materials"] = materials
    gltf["images"] = images
    gltf["textures"] = textures
    return GlbData(json=gltf, bin_chunk=glb.bin_chunk)


def _reapply_patched_material_policy(
    glb: GlbData,
    *,
    patched_materials: dict[str, Path],
    source_render_classes: dict[str, str],
) -> None:
    """Re-classify swapped materials; preserve uniform_decal from the source GLB."""
    if not patched_materials:
        return
    materials = glb.json.get("materials") or []
    geom_stats = compute_material_geometry_stats(glb)
    name_to_idx: dict[str, int] = {}
    for mat_idx, material in enumerate(materials):
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or "").strip().casefold()
        if name:
            name_to_idx[name] = mat_idx

    for mat_name, tex_path in patched_materials.items():
        key = str(mat_name or "").strip().casefold()
        mat_idx = name_to_idx.get(key)
        if mat_idx is None or mat_idx >= len(materials):
            continue
        material = materials[mat_idx]
        if not isinstance(material, dict):
            continue
        _reset_material_opacity(material)
        tex_bytes = tex_path.read_bytes() if tex_path.is_file() else None
        result = classify_material(
            material,
            texture_bytes=tex_bytes,
            geometry_stats=geom_stats.get(mat_idx),
        )
        preserved = source_render_classes.get(key)
        if (
            preserved == "uniform_decal"
            and not texture_has_partial_alpha_channel(tex_bytes)
        ):
            result = ClassificationResult(
                render_class=RenderClass.UNIFORM_DECAL,
                texture_meaningful_alpha=result.texture_meaningful_alpha,
                horizontal_face_fraction=result.horizontal_face_fraction,
                nitro_alpha=result.nitro_alpha,
            )
        apply_render_class_to_material(material, result)


def write_patched_preview_glb(
    source_glb: Path,
    output_glb: Path,
    *,
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
) -> Path:
    """Write a preview GLB with per-material PNG URIs taken from resolved paths."""
    source_glb = source_glb.resolve()
    output_glb = output_glb.resolve()
    output_glb.parent.mkdir(parents=True, exist_ok=True)

    glb = read_glb(source_glb)
    materials_by_name = _materials_by_name(glb.json)
    source_render_classes = _source_render_classes(glb)

    material_images: dict[str, str] = {}
    patched_materials: dict[str, Path] = {}
    for label, tex_path in zip(mesh_labels, mesh_texture_paths):
        if not label or tex_path is None or not Path(tex_path).is_file():
            continue
        material = materials_by_name.get(str(label).strip().casefold())
        uri_name = ensure_colocated_texture(Path(tex_path), output_glb.parent, material=material)
        material_images[str(label)] = uri_name
        patched_materials[str(label)] = output_glb.parent / uri_name

    patched = patch_glb_material_textures(glb, material_images)
    _reapply_patched_material_policy(
        patched,
        patched_materials=patched_materials,
        source_render_classes=source_render_classes,
    )
    patched.write(output_glb)
    return output_glb


def write_flipbook_preview_glbs(
    source_glb: Path,
    output_dir: Path,
    *,
    material_name: str,
    frame_paths: list[Path],
    base_mesh_paths: list[Path | None],
    mesh_labels: list[str],
) -> list[Path]:
    """Write one patched GLB per animation frame (single material swap each)."""
    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for frame_idx, frame_path in enumerate(frame_paths):
        if not frame_path.is_file():
            continue
        combined: list[Path | None] = []
        mat_key = str(material_name or "").strip().casefold()
        for label, base_path in zip(mesh_labels, base_mesh_paths):
            if str(label or "").strip().casefold() == mat_key:
                combined.append(frame_path)
            else:
                combined.append(base_path)
        out_path = output_dir / f"frame_{frame_idx:03d}.glb"
        write_patched_preview_glb(
            source_glb,
            out_path,
            mesh_labels=mesh_labels,
            mesh_texture_paths=combined,
        )
        paths.append(out_path)
    return paths
