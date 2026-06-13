"""CPU snapshot renderer using the same mesh prep as the GL preview viewport."""
from __future__ import annotations

import io
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..glb_preview_textures import (
    MaterialPreviewState,
    apply_material_preview_alpha,
    attach_preview_textures,
    build_mesh_texture_paths,
    discover_colocated_textures,
    extract_named_meshes,
    merge_texture_by_name,
    merge_texture_paths,
    ordered_texture_paths_from_glb,
    parse_glb_material_preview_states,
    parse_glb_material_texture_map,
)
from .pipeline import ModelPreviewBundle

_PREVIEW_MAX_TEXELS_PER_FACE = 12_000
_PREVIEW_MAX_TEXELS_PER_MESH = 400_000

# Match glb_viewer.html defaults (viewport orthographic catalog view).
DEFAULT_YAW_DEG = 35.0
DEFAULT_PITCH_DEG = 28.0
DEFAULT_ZOOM_FACTOR = 1.0


@dataclass
class _DrawMesh:
    vertices: np.ndarray
    faces: np.ndarray
    blend_mode: str
    center: np.ndarray
    texture: np.ndarray | None = None
    texcoords: np.ndarray | None = None
    material_state: MaterialPreviewState | None = None
    colors: np.ndarray | None = None


@dataclass(frozen=True)
class PreviewTextureContext:
    texture_by_name: dict[str, Path]
    material_to_texture: dict[str, str]
    texture_bind_order: tuple[str, ...]
    fallback_paths: tuple[Path, ...]
    mesh_texture_paths: tuple[Path | None, ...]
    material_states: dict[str, MaterialPreviewState]
    image_cache: dict[str, np.ndarray]


def render_model_preview_snapshot(
    bundle: ModelPreviewBundle,
    *,
    width: int = 128,
    height: int = 128,
    yaw_deg: float = DEFAULT_YAW_DEG,
    pitch_deg: float = DEFAULT_PITCH_DEG,
    zoom_factor: float = DEFAULT_ZOOM_FACTOR,
) -> tuple[bytes | None, int | None, int | None]:
    """Rasterize a patched preview GLB the same way the viewport bakes pixel art."""
    try:
        import trimesh
    except ImportError:
        return None, None, None

    try:
        loaded = trimesh.load(str(bundle.patched_glb), force="scene")
    except Exception:
        return None, None, None
    if loaded is None:
        return None, None, None

    named_meshes = extract_named_meshes(loaded)
    if not named_meshes:
        return None, None, None

    ctx = _build_texture_context(bundle, named_meshes)
    attach_preview_textures(named_meshes, list(ctx.mesh_texture_paths))
    draw_meshes = _build_draw_meshes(named_meshes, ctx)
    if not draw_meshes:
        return None, None, None

    return _render_draw_meshes(
        draw_meshes,
        width=max(16, width),
        height=max(16, height),
        yaw_deg=yaw_deg,
        pitch_deg=pitch_deg,
        zoom_factor=zoom_factor,
    )


def texture_baked_display_geometry(
    mesh,
    vertices: np.ndarray,
    faces: np.ndarray,
    *,
    image: object | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Shared pixel-art UV bake used by the viewport GL path and EasyFind snapshots."""
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None
    uv = getattr(visual, "uv", None)
    if getattr(visual, "kind", None) != "texture" and uv is None:
        return None
    if image is None or uv is None:
        return None
    uv_arr = np.asarray(uv, dtype=float)
    if uv_arr.ndim != 2 or uv_arr.shape[1] < 2 or len(faces) == 0:
        return None

    try:
        img = image_rgba_uint8(image)
        if img is None:
            return None
        height, width = int(img.shape[0]), int(img.shape[1])
        if width <= 0 or height <= 0:
            return None

        face_uvs = face_uv_corners(mesh, faces, uv_arr)
        if face_uvs is None:
            return None

        source_faces = np.asarray(mesh.faces, dtype=int)
        out_vertices: list[np.ndarray] = []
        out_faces: list[list[int]] = []
        out_colors: list[np.ndarray] = []
        vert_offset = 0
        texel_count = 0
        use_corners_only = False

        for face_index, face in enumerate(source_faces):
            tri_3d = vertices[face]
            tri_uv = face_uvs[face_index]
            tri_px = np.column_stack(
                (
                    np.mod(tri_uv[:, 0], 1.0) * width,
                    (1.0 - np.mod(tri_uv[:, 1], 1.0)) * height,
                )
            )

            min_x = int(np.floor(tri_px[:, 0].min()))
            max_x = int(np.ceil(tri_px[:, 0].max()))
            min_y = int(np.floor(tri_px[:, 1].min()))
            max_y = int(np.ceil(tri_px[:, 1].max()))
            if max_x < min_x or max_y < min_y:
                continue

            face_texels = (max_x - min_x + 1) * (max_y - min_y + 1)
            if (
                use_corners_only
                or face_texels > _PREVIEW_MAX_TEXELS_PER_FACE
                or texel_count + face_texels > _PREVIEW_MAX_TEXELS_PER_MESH
            ):
                corner_colors = sample_image_array_at_px(tri_px, img)
                if corner_colors is None:
                    continue
                out_vertices.append(np.asarray(tri_3d, dtype=float))
                out_colors.extend(corner_colors)
                out_faces.append([vert_offset, vert_offset + 1, vert_offset + 2])
                vert_offset += 3
                if texel_count + face_texels > _PREVIEW_MAX_TEXELS_PER_MESH:
                    use_corners_only = True
                continue

            for iy in range(min_y, max_y + 1):
                for ix in range(min_x, max_x + 1):
                    center = np.array((ix + 0.5, iy + 0.5), dtype=float)
                    weights = barycentric_weights_2d(center, tri_px)
                    if weights is None or weights.min() < -1e-5:
                        continue

                    tex_x = int(ix) % width
                    tex_y = int(iy) % height
                    color = img[tex_y, tex_x].astype(float) / 255.0

                    quad_px = np.array(
                        (
                            (ix, iy),
                            (ix + 1, iy),
                            (ix + 1, iy + 1),
                            (ix, iy + 1),
                        ),
                        dtype=float,
                    )
                    quad_3d = []
                    for px_corner in quad_px:
                        corner_w = barycentric_weights_2d(px_corner, tri_px)
                        if corner_w is None:
                            corner_w = weights
                        quad_3d.append(corner_w @ tri_3d)
                    quad_3d_arr = np.asarray(quad_3d, dtype=float)

                    out_vertices.append(quad_3d_arr)
                    out_colors.extend([color] * 4)
                    out_faces.append([vert_offset, vert_offset + 1, vert_offset + 2])
                    out_faces.append([vert_offset, vert_offset + 2, vert_offset + 3])
                    vert_offset += 4
                    texel_count += 1

        if not out_vertices:
            return None
        display_vertices = np.vstack(out_vertices)
        display_faces = np.asarray(out_faces, dtype=int)
        colors = np.asarray(out_colors, dtype=float)
        return display_vertices, display_faces, colors
    except Exception:
        return None


def _build_texture_context(
    bundle: ModelPreviewBundle,
    named_meshes: list[tuple[str, object]],
) -> PreviewTextureContext:
    """Resolve mesh→texture paths for trimesh dump order (matches legacy GL preview)."""
    glb_path = bundle.patched_glb
    colocated = discover_colocated_textures(glb_path)
    glb_material_map = parse_glb_material_texture_map(glb_path)
    merged_names = merge_texture_by_name(bundle.texture_by_name, colocated, glb_material_map)
    unique_fallback = merge_texture_paths(
        ordered_texture_paths_from_glb(glb_path, colocated),
        list(bundle.fallback_paths),
    )
    mesh_texture_paths = tuple(
        build_mesh_texture_paths(
            named_meshes,
            glb_path=glb_path,
            texture_by_name=merged_names,
            material_to_texture=bundle.material_to_texture,
            texture_bind_order=list(bundle.texture_bind_order),
            fallback_paths=unique_fallback,
        )
    )

    return PreviewTextureContext(
        texture_by_name=merged_names,
        material_to_texture=bundle.material_to_texture,
        texture_bind_order=bundle.texture_bind_order,
        fallback_paths=bundle.fallback_paths,
        mesh_texture_paths=mesh_texture_paths,
        material_states=parse_glb_material_preview_states(glb_path),
        image_cache={},
    )


def _normalize_vertex_colors(colors: np.ndarray) -> np.ndarray:
    """Force per-vertex RGBA into 0..1 floats (handles 0..255 inputs)."""
    arr = np.asarray(colors, dtype=float)
    if arr.ndim != 2:
        arr = arr.reshape(-1, arr.shape[-1])
    if arr.shape[1] < 4:
        alpha = np.ones((len(arr), 1), dtype=float)
        arr = np.column_stack([arr[:, :3], alpha])
    if np.nanmax(arr[:, :3]) > 1.0:
        arr = arr / 255.0
    arr[:, :4] = np.clip(arr[:, :4], 0.0, 1.0)
    return arr


def _rgba_float_to_u8(color: np.ndarray) -> tuple[int, int, int, int]:
    """Clamp interpolated RGBA to valid PNG bytes."""
    channels = np.asarray(color, dtype=float).reshape(-1)[:4]
    if channels.size < 4:
        channels = np.append(channels, [1.0] * (4 - channels.size))
    if float(np.nanmax(channels[:3])) > 1.0:
        channels = channels / 255.0
    scaled = np.clip(np.rint(channels * 255.0), 0, 255).astype(np.int32)
    return int(scaled[0]), int(scaled[1]), int(scaled[2]), int(scaled[3])


def _build_draw_meshes(
    named_meshes: list[tuple[str, object]],
    ctx: PreviewTextureContext,
) -> list[_DrawMesh]:
    rotated_vertices = []
    for _name, mesh in named_meshes:
        vertices = apply_preview_orientation(np.asarray(mesh.vertices, dtype=float))
        rotated_vertices.append(vertices)

    all_vertices = np.vstack(rotated_vertices)
    center = all_vertices.mean(axis=0)
    extent = float(np.max(np.ptp(all_vertices - center, axis=0))) or 1.0
    scale = 40.0 / extent

    mesh_items: list[tuple[_DrawMesh, str]] = []
    for mesh_index, ((geom_name, mesh), vertices) in enumerate(zip(named_meshes, rotated_vertices)):
        if len(mesh.faces) == 0:
            continue
        vertices = (vertices - center) * scale
        faces = np.asarray(mesh.faces, dtype=int)
        material_state = material_preview_state(mesh, geom_name, ctx.material_states)
        image = visual_image(mesh, mesh_index=mesh_index, geometry_name=geom_name, ctx=ctx)
        tex_arr = image_rgba_uint8(image) if image is not None else None

        expanded = expand_textured_corners(mesh, vertices, faces)
        if expanded is not None and tex_arr is not None:
            flat_v, flat_uv, flat_f = expanded
            blend_mode = preview_blend_mode(material_state, tex_arr, None)
            mesh_items.append((
                _DrawMesh(
                    flat_v,
                    flat_f,
                    blend_mode,
                    np.mean(flat_v, axis=0),
                    texture=tex_arr,
                    texcoords=flat_uv,
                    material_state=material_state,
                ),
                blend_mode,
            ))
            continue

        colors = mesh_vertex_colors(mesh, vertices, mesh_index=mesh_index, geometry_name=geom_name, ctx=ctx)
        colors = apply_material_preview_alpha(colors, material_state)
        if colors is None:
            colors = np.full((len(vertices), 4), 0.5)
        colors = _normalize_vertex_colors(colors)
        blend_mode = preview_blend_mode(material_state, tex_arr, colors)
        mesh_items.append((
            _DrawMesh(
                vertices,
                faces,
                blend_mode,
                np.mean(vertices, axis=0),
                colors=colors,
                material_state=material_state,
            ),
            blend_mode,
        ))

    return _order_draw_meshes(mesh_items, yaw_deg=DEFAULT_YAW_DEG, pitch_deg=DEFAULT_PITCH_DEG)


def expand_textured_corners(
    mesh,
    vertices: np.ndarray,
    faces: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Duplicate triangle corners for per-pixel nearest-neighbor texturing (GL preview path)."""
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None
    uv = getattr(visual, "uv", None)
    if getattr(visual, "kind", None) != "texture" and uv is None:
        return None
    if uv is None:
        return None
    uv_arr = np.asarray(uv, dtype=float)
    face_uvs = face_uv_corners(mesh, faces, uv_arr)
    if face_uvs is None:
        return None

    source_faces = np.asarray(mesh.faces, dtype=int)
    flat_v: list[np.ndarray] = []
    flat_uv: list[np.ndarray] = []
    flat_f: list[list[int]] = []
    offset = 0
    for face_index, face in enumerate(source_faces):
        tri_v = vertices[face]
        tri_uv = face_uvs[face_index].copy()
        tri_uv[:, 0] = np.mod(tri_uv[:, 0], 1.0)
        tri_uv[:, 1] = 1.0 - np.mod(tri_uv[:, 1], 1.0)
        flat_v.extend(tri_v)
        flat_uv.extend(tri_uv)
        flat_f.append([offset, offset + 1, offset + 2])
        offset += 3

    if not flat_f:
        return None
    return (
        np.asarray(flat_v, dtype=float),
        np.asarray(flat_uv, dtype=float),
        np.asarray(flat_f, dtype=int),
    )


def _render_draw_meshes(
    draw_meshes: list[_DrawMesh],
    *,
    width: int,
    height: int,
    yaw_deg: float,
    pitch_deg: float,
    zoom_factor: float,
) -> tuple[bytes | None, int | None, int | None]:
    from PIL import Image

    all_xyz = np.vstack([mesh.vertices for mesh in draw_meshes])
    bounds_min = all_xyz.min(axis=0)
    bounds_max = all_xyz.max(axis=0)
    center = (bounds_min + bounds_max) * 0.5
    size = bounds_max - bounds_min
    max_dim = float(max(size.max(), 1e-3))

    eye, _target = camera_eye_and_center(bounds_min, bounds_max, yaw_deg=yaw_deg, pitch_deg=pitch_deg)
    pose = look_at(eye, center)
    view = np.linalg.inv(pose)

    rgba = np.zeros((height, width, 4), dtype=np.uint8)
    zbuf = np.full((height, width), np.inf, dtype=np.float32)

    aspect = width / max(height, 1)
    half_h = (max_dim * 0.55) / max(zoom_factor, 1e-3)
    half_w = half_h * aspect
    margin = 4.0
    drawable_w = max(width - 2 * margin, 1.0)
    drawable_h = max(height - 2 * margin, 1.0)

    all_cam = _world_to_camera(all_xyz, view)
    cx = float(all_cam[:, 0].mean())
    cy = float(all_cam[:, 1].mean())

    def project(x: float, y: float) -> tuple[float, float]:
        sx = margin + ((x - cx) / (2.0 * half_w) + 0.5) * drawable_w
        sy = margin + (0.5 - (y - cy) / (2.0 * half_h)) * drawable_h
        return sx, sy

    for draw_mesh in draw_meshes:
        cam = _world_to_camera(draw_mesh.vertices, view)
        for face in draw_mesh.faces:
            tri_cam = cam[face]
            tri_xy = tri_cam[:, :2]
            screen = np.array([project(x, y) for x, y in tri_xy], dtype=float)
            min_x = max(0, int(math.floor(screen[:, 0].min())))
            max_x = min(width - 1, int(math.ceil(screen[:, 0].max())))
            min_y = max(0, int(math.floor(screen[:, 1].min())))
            max_y = min(height - 1, int(math.ceil(screen[:, 1].max())))
            v0, v1, v2 = screen
            z0, z1, z2 = tri_cam[:, 2]
            tri_uv = draw_mesh.texcoords[face] if draw_mesh.texcoords is not None else None
            tri_colors = draw_mesh.colors[face] if draw_mesh.colors is not None else None
            if tri_colors is not None:
                if tri_colors.ndim == 1:
                    tri_colors = np.tile(tri_colors, (3, 1))
                if tri_colors.shape[1] == 3:
                    tri_colors = np.column_stack([tri_colors, np.ones(3)])

            for py in range(min_y, max_y + 1):
                for px in range(min_x, max_x + 1):
                    bc = barycentric_weights_2d((px + 0.5, py + 0.5), tuple(v0), tuple(v1), tuple(v2))
                    if bc is None:
                        continue
                    w0, w1, w2 = bc
                    z = w0 * z0 + w1 * z1 + w2 * z2
                    if z >= zbuf[py, px]:
                        continue

                    if draw_mesh.texture is not None and tri_uv is not None:
                        u = w0 * tri_uv[0, 0] + w1 * tri_uv[1, 0] + w2 * tri_uv[2, 0]
                        v = w0 * tri_uv[0, 1] + w1 * tri_uv[1, 1] + w2 * tri_uv[2, 1]
                        r, g, b, a = sample_texture_nearest(
                            draw_mesh.texture,
                            u,
                            v,
                            draw_mesh.material_state,
                        )
                    elif tri_colors is not None:
                        color = w0 * tri_colors[0] + w1 * tri_colors[1] + w2 * tri_colors[2]
                        r, g, b, a = _rgba_float_to_u8(color)
                    else:
                        continue

                    if a <= 0:
                        continue
                    if draw_mesh.blend_mode == "cutout" and a < 128:
                        continue
                    zbuf[py, px] = z
                    rgba[py, px, 0] = r
                    rgba[py, px, 1] = g
                    rgba[py, px, 2] = b
                    rgba[py, px, 3] = a

    image = Image.fromarray(rgba, mode="RGBA")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue(), width, height


def apply_preview_orientation(vertices: np.ndarray) -> np.ndarray:
    x = vertices[:, 0].copy()
    y = vertices[:, 1].copy()
    z = vertices[:, 2].copy()
    return np.column_stack((x, z, y))


def image_rgba_uint8(image) -> np.ndarray | None:
    try:
        if hasattr(image, "convert"):
            image = image.convert("RGBA")
        img = np.asarray(image, dtype=np.uint8)
        if img.ndim != 3 or img.shape[0] <= 0 or img.shape[1] <= 0:
            return None
        if img.shape[2] == 3:
            alpha = np.full((img.shape[0], img.shape[1], 1), 255, dtype=np.uint8)
            img = np.concatenate([img, alpha], axis=2)
        return img[:, :, :4]
    except Exception:
        return None


def face_uv_corners(mesh, faces, uv_arr: np.ndarray) -> np.ndarray | None:
    source_faces = np.asarray(mesh.faces, dtype=int)
    corner_count = int(source_faces.size)
    if len(uv_arr) == len(mesh.vertices):
        return uv_arr[source_faces][:, :, :2]
    if len(uv_arr) == len(source_faces) * 3:
        return uv_arr.reshape(len(source_faces), 3, 2)
    if len(uv_arr) == corner_count:
        return uv_arr.reshape(len(source_faces), 3, 2)
    if len(uv_arr) == len(faces) * 3:
        return uv_arr.reshape(len(faces), 3, 2)
    return None


def sample_texture_nearest(
    tex: np.ndarray,
    u: float,
    v: float,
    material_state: MaterialPreviewState | None,
) -> tuple[int, int, int, int]:
    """GL_NEAREST-style sampling — matches the WebEngine / GLTexturedMeshItem path."""
    h, w = tex.shape[:2]
    px = int(round((u % 1.0) * (w - 1)))
    py = int(round((v % 1.0) * (h - 1)))
    px = max(0, min(w - 1, px))
    py = max(0, min(h - 1, py))
    row = tex[py, px].astype(float)
    rgba = np.array([[row[0], row[1], row[2], row[3]]], dtype=float) / 255.0
    adjusted = apply_material_preview_alpha(rgba, material_state)
    if adjusted is None or len(adjusted) == 0:
        adjusted = rgba
    out = np.clip(np.rint(adjusted[0] * 255.0), 0, 255).astype(np.int32)
    return int(out[0]), int(out[1]), int(out[2]), int(out[3])


def barycentric_weights_2d(point, v0, v1, v2) -> np.ndarray | None:
    try:
        triangle_px = np.asarray((v0, v1, v2), dtype=float)
        a, b, c = triangle_px
        v0v = c - a
        v1v = b - a
        v2v = np.asarray(point, dtype=float) - a
        dot00 = float(v0v @ v0v)
        dot01 = float(v0v @ v1v)
        dot02 = float(v0v @ v2v)
        dot11 = float(v1v @ v1v)
        dot12 = float(v1v @ v2v)
        denom = dot00 * dot11 - dot01 * dot01
        if abs(denom) < 1e-12:
            return None
        inv = 1.0 / denom
        u = (dot11 * dot02 - dot01 * dot12) * inv
        v = (dot00 * dot12 - dot01 * dot02) * inv
        return np.array((1.0 - u - v, v, u), dtype=float)
    except Exception:
        return None


def sample_image_array_at_px(px_coords: np.ndarray, img: np.ndarray) -> list[np.ndarray] | None:
    try:
        height, width = int(img.shape[0]), int(img.shape[1])
        px = np.clip(np.rint(px_coords[:, 0]).astype(int), 0, width - 1)
        py = np.clip(np.rint(px_coords[:, 1]).astype(int), 0, height - 1)
        return [row.astype(float) / 255.0 for row in img[py, px, :4]]
    except Exception:
        return None


def look_at(eye: np.ndarray, center: np.ndarray, up: np.ndarray | None = None) -> np.ndarray:
    up = np.array([0.0, 1.0, 0.0]) if up is None else np.asarray(up, dtype=float)
    forward = center - eye
    forward /= max(np.linalg.norm(forward), 1e-9)
    right = np.cross(forward, up)
    if np.linalg.norm(right) < 1e-9:
        right = np.array([1.0, 0.0, 0.0])
    right /= max(np.linalg.norm(right), 1e-9)
    true_up = np.cross(right, forward)
    pose = np.eye(4, dtype=float)
    pose[:3, 0] = right
    pose[:3, 1] = true_up
    pose[:3, 2] = -forward
    pose[:3, 3] = eye
    return pose


def camera_eye_and_center(
    bounds_min: np.ndarray,
    bounds_max: np.ndarray,
    *,
    yaw_deg: float,
    pitch_deg: float,
) -> tuple[np.ndarray, np.ndarray]:
    center = (bounds_min + bounds_max) * 0.5
    size = bounds_max - bounds_min
    max_dim = float(max(size.max(), 1e-3))
    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    dist = max_dim * 2.2
    eye = center + np.array([
        math.sin(yaw) * math.cos(pitch) * dist,
        math.sin(pitch) * dist,
        math.cos(yaw) * math.cos(pitch) * dist,
    ], dtype=float)
    return eye, center


def _world_to_camera(vertices: np.ndarray, view: np.ndarray) -> np.ndarray:
    hom = np.hstack([vertices, np.ones((len(vertices), 1), dtype=float)])
    return (view @ hom.T).T[:, :3]


def material_preview_state(mesh, geometry_name: str, states: dict[str, MaterialPreviewState]) -> MaterialPreviewState | None:
    visual = getattr(mesh, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    mat_name = str(getattr(material, "name", "") or "").strip().casefold()
    if mat_name and mat_name in states:
        return states[mat_name]
    if geometry_name:
        key = geometry_name.casefold()
        if key in states:
            return states[key]
    if material is not None:
        for attr in ("index", "material_id", "mat_id"):
            value = getattr(material, attr, None)
            if isinstance(value, int) and str(value) in states:
                return states[str(value)]
    return None


def visual_image(mesh, *, mesh_index: int, geometry_name: str, ctx: PreviewTextureContext):
    visual = getattr(mesh, "visual", None)
    material = getattr(visual, "material", None) if visual is not None else None
    image = getattr(material, "image", None) if material is not None else None
    if image is not None:
        return image
    if mesh_index < len(ctx.mesh_texture_paths) and ctx.mesh_texture_paths[mesh_index] is not None:
        return image_for_path(ctx.mesh_texture_paths[mesh_index], ctx.image_cache)
    if ctx.fallback_paths:
        return image_for_path(ctx.fallback_paths[mesh_index % len(ctx.fallback_paths)], ctx.image_cache)
    return None


def image_for_path(path: Path | None, cache: dict[str, np.ndarray]):
    if path is None or not path.is_file():
        return None
    key = str(path.resolve())
    if key in cache:
        from PIL import Image

        return Image.fromarray(cache[key])
    try:
        from PIL import Image

        image = Image.open(path).convert("RGBA")
    except Exception:
        return None
    arr = np.asarray(image, dtype=np.uint8)
    cache[key] = arr
    return image


def mesh_vertex_colors(mesh, vertices: np.ndarray, *, mesh_index: int, geometry_name: str, ctx: PreviewTextureContext):
    visual = getattr(mesh, "visual", None)
    if visual is None:
        return None
    if getattr(visual, "kind", None) == "texture":
        uv = getattr(visual, "uv", None)
        image = visual_image(mesh, mesh_index=mesh_index, geometry_name=geometry_name, ctx=ctx)
        if uv is not None and image is not None and len(uv) == len(mesh.vertices):
            img = image_rgba_uint8(image)
            if img is not None:
                px = np.clip(np.rint(uv[:, 0] * (img.shape[1] - 1)).astype(int), 0, img.shape[1] - 1)
                py = np.clip(np.rint((1.0 - uv[:, 1]) * (img.shape[0] - 1)).astype(int), 0, img.shape[0] - 1)
                return img[py, px, :4].astype(float) / 255.0
    main_color = getattr(visual, "main_color", None)
    if main_color is not None:
        rgba = np.asarray(main_color, dtype=float)
        if rgba.max() <= 1.0:
            return np.tile(rgba[:4] if rgba.size >= 4 else np.append(rgba[:3], 1.0), (len(vertices), 1))
    return None


def preview_blend_mode(
    material_state: MaterialPreviewState | None,
    img: np.ndarray | None,
    colors: np.ndarray | None,
) -> str:
    state = material_state or MaterialPreviewState()
    if state.render_class == "uniform_decal":
        return "shadow"
    if state.alpha_mode == "MASK":
        return "cutout"
    if state.alpha_mode == "BLEND":
        if colors_need_true_blend(colors):
            return "blend"
        return "cutout" if colors_need_translucent_blend(colors) else "opaque"
    if colors_need_true_blend(colors):
        return "blend"
    if colors_need_translucent_blend(colors):
        return "cutout"
    if img is not None:
        alpha = img[:, :, 3]
        if np.any((alpha > 8) & (alpha < 247)):
            return "blend"
        if np.any(alpha < 250):
            return "cutout"
    return "opaque"


def colors_need_true_blend(colors) -> bool:
    if colors is None:
        return False
    arr = np.asarray(colors, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 4 or len(arr) == 0:
        return False
    alpha = arr[:, 3]
    partial = alpha[(alpha > 0.03) & (alpha < 0.97)]
    return partial.size > 0


def colors_need_translucent_blend(colors) -> bool:
    if colors is None:
        return False
    arr = np.asarray(colors, dtype=float)
    if arr.ndim != 2 or arr.shape[1] < 4 or len(arr) == 0:
        return False
    return bool(np.any(arr[:, 3] < 0.995))


def _order_draw_meshes(
    mesh_items: list[tuple[_DrawMesh, str]],
    *,
    yaw_deg: float,
    pitch_deg: float,
) -> list[_DrawMesh]:
    shadows = [entry[0] for entry in mesh_items if entry[1] == "shadow"]
    solid = [entry[0] for entry in mesh_items if entry[1] not in {"blend", "shadow"}]
    blended = [entry[0] for entry in mesh_items if entry[1] == "blend"]

    yaw = math.radians(yaw_deg)
    pitch = math.radians(pitch_deg)
    eye_dir = np.array([
        math.sin(yaw) * math.cos(pitch),
        math.sin(pitch),
        math.cos(yaw) * math.cos(pitch),
    ], dtype=float)

    def camera_distance(mesh: _DrawMesh) -> float:
        return float(np.dot(mesh.center, eye_dir))

    solid.sort(key=camera_distance)
    blended.sort(key=camera_distance, reverse=True)
    return shadows + solid + blended
