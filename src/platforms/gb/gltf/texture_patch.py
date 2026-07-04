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


def stage_textures_beside_glb(directory: Path, texture_paths: list[Path]) -> None:
    """Copy resolver/apicula PNGs beside the preview GLB so GLTFLoader can fetch them."""
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    for src in texture_paths:
        path = Path(src)
        if not path.is_file():
            continue
        dest = directory / path.name
        if dest.is_file():
            if dest.resolve() == path.resolve():
                continue
            try:
                if path.stat().st_size <= dest.stat().st_size:
                    continue
            except OSError:
                continue
        shutil.copy2(path, dest)


def _texture_search_index(search_paths: list[Path]) -> dict[str, Path]:
    index: dict[str, Path] = {}
    for raw in search_paths:
        path = Path(raw)
        if path.is_file():
            index.setdefault(path.name.casefold(), path)
            continue
        if not path.is_dir():
            continue
        for pattern in ("*.png", "*.PNG", "*.bmp", "*.BMP", "*.tga", "*.TGA"):
            for candidate in path.rglob(pattern):
                if candidate.is_file():
                    index.setdefault(candidate.name.casefold(), candidate)
    return index


def ensure_patched_glb_textures_resolvable(
    glb_path: Path,
    *,
    search_paths: list[Path],
) -> None:
    """Copy every external image URI referenced by *glb_path* beside the GLB."""
    glb_path = glb_path.resolve()
    directory = glb_path.parent
    index = _texture_search_index(search_paths)
    glb = read_glb(glb_path)
    for image in glb.json.get("images") or []:
        if not isinstance(image, dict):
            continue
        uri = str(image.get("uri") or "").strip()
        if not uri or uri.startswith("data:"):
            continue
        basename = Path(uri).name
        dest = directory / basename
        if dest.is_file():
            continue
        src = index.get(basename.casefold())
        if src is not None and src.is_file():
            shutil.copy2(src, dest)


def _resolve_texture_for_material(
    material_name: str,
    material_index: int,
    *,
    part_paths: dict[str, Path],
    texture_by_name: dict[str, Path],
    material_to_texture: dict[str, str],
) -> Path | None:
    key = str(material_name or "").strip().casefold()
    if not key:
        return None
    path = part_paths.get(key)
    if path is not None and path.is_file():
        return path
    path = texture_by_name.get(key)
    if path is not None and path.is_file():
        return path
    bound = material_to_texture.get(key) or material_to_texture.get(material_name)
    if bound:
        path = texture_by_name.get(str(bound).strip().casefold())
        if path is not None and path.is_file():
            return path
    path = texture_by_name.get(str(material_index))
    if path is not None and path.is_file():
        return path
    return None


def build_material_texture_path_map(
    source_glb: Path,
    *,
    mesh_labels: list[str],
    mesh_texture_paths: list[Path | None],
    texture_by_name: dict[str, Path] | None = None,
    material_to_texture: dict[str, str] | None = None,
) -> dict[str, Path]:
    """Map every glTF material name to a resolved PNG path for web preview."""
    texture_by_name_cf = {
        str(name).casefold(): Path(path)
        for name, path in (texture_by_name or {}).items()
    }
    material_to_texture_cf = {
        str(name).casefold(): str(texture)
        for name, texture in (material_to_texture or {}).items()
    }
    part_paths: dict[str, Path] = {}
    for label, tex_path in zip(mesh_labels, mesh_texture_paths):
        if not label or tex_path is None:
            continue
        path = Path(tex_path)
        if path.is_file():
            part_paths[str(label).strip().casefold()] = path

    glb = read_glb(source_glb)
    out: dict[str, Path] = {}
    materials = glb.json.get("materials") or []

    mat_index_to_label: dict[int, str] = {}
    part_index = 0
    for mesh in glb.json.get("meshes") or []:
        if not isinstance(mesh, dict):
            continue
        mesh_name = str(mesh.get("name") or "").strip()
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            label = mesh_name
            if part_index < len(mesh_labels) and str(mesh_labels[part_index] or "").strip():
                label = str(mesh_labels[part_index]).strip()
            material_index = primitive.get("material")
            if isinstance(material_index, int):
                mat_index_to_label.setdefault(material_index, label)
            part_index += 1

    if not materials and part_paths:
        for label, path in part_paths.items():
            out[label] = path
        return out

    for mat_idx, material in enumerate(materials):
        if not isinstance(material, dict):
            continue
        name = str(material.get("name") or "").strip()
        if not name:
            name = str(mat_index_to_label.get(mat_idx) or "").strip()
        if not name and mat_idx < len(mesh_labels):
            name = str(mesh_labels[mat_idx] or "").strip()
        if not name:
            continue
        resolved = _resolve_texture_for_material(
            name,
            mat_idx,
            part_paths=part_paths,
            texture_by_name=texture_by_name_cf,
            material_to_texture=material_to_texture_cf,
        )
        if resolved is not None:
            out[name] = resolved
    return out


def ensure_glb_materials_for_mesh_parts(glb: GlbData, mesh_labels: list[str]) -> GlbData:
    """Add or normalize materials for Unity/AssetStudio exports."""
    gltf = copy.deepcopy(glb.json)
    images = list(gltf.get("images") or [])
    if any(isinstance(image, dict) and image.get("bufferView") is not None for image in images):
        gltf["images"] = []
        gltf["textures"] = []

    materials = list(gltf.get("materials") or [])
    unnamed_materials = not materials or all(
        not str(material.get("name") or "").strip()
        for material in materials
        if isinstance(material, dict)
    )

    if materials and not unnamed_materials:
        part_index = 0
        for mesh in gltf.get("meshes") or []:
            if not isinstance(mesh, dict):
                continue
            mesh_name = str(mesh.get("name") or "").strip()
            for primitive in mesh.get("primitives") or []:
                if not isinstance(primitive, dict):
                    continue
                material_index = primitive.get("material")
                if not isinstance(material_index, int) or not (0 <= material_index < len(materials)):
                    part_index += 1
                    continue
                material = materials[material_index]
                if not isinstance(material, dict):
                    part_index += 1
                    continue
                if not str(material.get("name") or "").strip():
                    label = mesh_name
                    if part_index < len(mesh_labels) and str(mesh_labels[part_index] or "").strip():
                        label = str(mesh_labels[part_index]).strip()
                    material["name"] = label or f"material_{material_index}"
                pbr = material.setdefault("pbrMetallicRoughness", {})
                if isinstance(pbr, dict):
                    pbr.setdefault("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
                material["doubleSided"] = True
                part_index += 1
        gltf["materials"] = materials
        return GlbData(json=gltf, bin_chunk=glb.bin_chunk)

    if materials and unnamed_materials:
        part_index = 0
        for mesh in gltf.get("meshes") or []:
            if not isinstance(mesh, dict):
                continue
            mesh_name = str(mesh.get("name") or "").strip()
            for primitive in mesh.get("primitives") or []:
                if not isinstance(primitive, dict):
                    continue
                material_index = primitive.get("material")
                if not isinstance(material_index, int) or not (0 <= material_index < len(materials)):
                    part_index += 1
                    continue
                material = materials[material_index]
                if not isinstance(material, dict):
                    part_index += 1
                    continue
                label = mesh_name
                if part_index < len(mesh_labels) and str(mesh_labels[part_index] or "").strip():
                    label = str(mesh_labels[part_index]).strip()
                material["name"] = label or f"material_{material_index}"
                pbr = material.setdefault("pbrMetallicRoughness", {})
                if isinstance(pbr, dict):
                    pbr.setdefault("baseColorFactor", [1.0, 1.0, 1.0, 1.0])
                material["doubleSided"] = True
                part_index += 1
        gltf["materials"] = materials
        return GlbData(json=gltf, bin_chunk=glb.bin_chunk)

    materials_out: list[dict] = []
    meshes = gltf.get("meshes") or []
    part_index = 0
    for mesh in meshes:
        if not isinstance(mesh, dict):
            continue
        mesh_name = str(mesh.get("name") or "").strip()
        primitives = mesh.get("primitives") or []
        if not primitives:
            primitives = [{}]
            mesh["primitives"] = primitives
        for primitive in primitives:
            if not isinstance(primitive, dict):
                continue
            label = mesh_name
            if part_index < len(mesh_labels) and str(mesh_labels[part_index] or "").strip():
                label = str(mesh_labels[part_index]).strip()
            part_index += 1
            mat_idx = len(materials_out)
            materials_out.append(
                {
                    "name": label or f"material_{mat_idx}",
                    "pbrMetallicRoughness": {
                        "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                        "metallicFactor": 0.0,
                        "roughnessFactor": 0.9,
                    },
                    "doubleSided": True,
                }
            )
            primitive["material"] = mat_idx
    gltf["materials"] = materials_out
    return GlbData(json=gltf, bin_chunk=glb.bin_chunk)


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
    if textures:
        # Preserve source samplers: 3DS/GF models rely on REPEAT/MIRRORED_REPEAT
        # for baked UV transforms (mirrored body halves). Only textures without
        # a sampler get the clamp default (apicula-style atlas PNGs).
        samplers = list(gltf.get("samplers") or [])
        default_sampler: int | None = None
        for texture in textures:
            if not isinstance(texture, dict) or texture.get("sampler") is not None:
                continue
            if default_sampler is None:
                samplers.append(
                    {
                        "magFilter": 9729,
                        "minFilter": 9729,
                        "wrapS": 33071,
                        "wrapT": 33071,
                    }
                )
                default_sampler = len(samplers) - 1
            texture["sampler"] = default_sampler
        gltf["samplers"] = samplers
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
    texture_by_name: dict[str, Path] | None = None,
    material_to_texture: dict[str, str] | None = None,
    stage_texture_paths: list[Path] | None = None,
) -> Path:
    """Write a preview GLB with per-material PNG URIs taken from resolved paths."""
    source_glb = source_glb.resolve()
    output_glb = output_glb.resolve()
    output_glb.parent.mkdir(parents=True, exist_ok=True)
    if stage_texture_paths:
        stage_textures_beside_glb(output_glb.parent, stage_texture_paths)

    glb = read_glb(source_glb)
    glb = ensure_glb_materials_for_mesh_parts(glb, mesh_labels)
    materials_by_name = _materials_by_name(glb.json)
    source_render_classes = _source_render_classes(glb)

    material_tex_paths = build_material_texture_path_map(
        source_glb,
        mesh_labels=mesh_labels,
        mesh_texture_paths=mesh_texture_paths,
        texture_by_name=texture_by_name,
        material_to_texture=material_to_texture,
    )
    if glb.json.get("materials"):
        remapped: dict[str, Path] = {}
        for material in glb.json["materials"]:
            if not isinstance(material, dict):
                continue
            name = str(material.get("name") or "").strip()
            if not name:
                continue
            path = material_tex_paths.get(name) or material_tex_paths.get(name.casefold())
            if path is not None:
                remapped[name] = path
        if remapped:
            material_tex_paths = remapped

    material_images: dict[str, str] = {}
    patched_materials: dict[str, Path] = {}
    for mat_name, tex_path in material_tex_paths.items():
        material = materials_by_name.get(str(mat_name).strip().casefold())
        uri_name = ensure_colocated_texture(Path(tex_path), output_glb.parent, material=material)
        material_images[str(mat_name)] = uri_name
        patched_materials[str(mat_name)] = output_glb.parent / uri_name

    patched = patch_glb_material_textures(glb, material_images)
    _reapply_patched_material_policy(
        patched,
        patched_materials=patched_materials,
        source_render_classes=source_render_classes,
    )
    patched.write(output_glb)
    ensure_patched_glb_textures_resolvable(
        output_glb,
        search_paths=[
            *(stage_texture_paths or []),
            source_glb.parent,
            output_glb.parent,
        ],
    )
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
