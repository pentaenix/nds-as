"""Converted model/texture preview quality scoring."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

@dataclass(slots=True)
class TextureQuality:
    score: int
    mesh_faces: int
    texture_visuals: int
    image_count: int
    uv_sets: int
    sampled_unique_colors: int
    image_unique_colors: int
    flat_images: int

    @property
    def confident(self) -> bool:
        # A converted GLB can contain material slots or even placeholder image
        # objects without visibly texturing the model. Treat it as confirmed only
        # when the UV-sampled preview would actually have color variation.
        return self.mesh_faces > 0 and self.image_count > 0 and self.uv_sets > 0 and self.sampled_unique_colors >= 8 and self.score >= 55

    @property
    def weak_material_only(self) -> bool:
        return self.mesh_faces > 0 and self.score > 0 and not self.confident

    def summary(self) -> str:
        base = (
            f"texture quality {self.score}; mesh faces {self.mesh_faces}; "
            f"images {self.image_count}; UV sets {self.uv_sets}; "
            f"sampled colors {self.sampled_unique_colors}; image colors {self.image_unique_colors}"
        )
        if self.confident:
            return base + "; verified visible texture"
        if self.weak_material_only:
            return base + "; weak material evidence only"
        return base + "; no verified texture"

@dataclass(slots=True)
class CachedTextureResolution:
    report: str
    selected_texture_id: str
    preview_path: Path
    auxiliary_paths: list[Path]
    texture_by_name: dict[str, str] = field(default_factory=dict)
    material_to_texture: dict[str, str] = field(default_factory=dict)
    texture_bind_order: list[str] = field(default_factory=list)

def converted_texture_quality(path: Path) -> TextureQuality:
    """Inspect a converted GLB/DAE and estimate whether a texture is actually visible.

    This is stricter than the old material-slot score. A GLB can have a texture
    visual/material object but still render gray if the image is flat, UVs are not
    usable, or the candidate texture archive is just the wrong one. The quality
    object lets RAE say “not sure” instead of pretending a candidate is correct.
    """
    try:
        import numpy as np
        import trimesh

        from ..glb_preview_textures import attach_preview_textures, build_mesh_texture_paths, discover_colocated_textures

        loaded = trimesh.load(path, force="scene")
        meshes = loaded.dump() if isinstance(loaded, trimesh.Scene) else [loaded]
        named_meshes: list[tuple[str, object]] = []
        for idx, mesh in enumerate(meshes):
            if hasattr(mesh, "faces") and hasattr(mesh, "vertices"):
                meta = getattr(mesh, "metadata", {}) or {}
                mat = getattr(getattr(getattr(mesh, "visual", None), "material", None), "name", "")
                name = str(mat or meta.get("name", "") or f"mesh_{idx}")
                named_meshes.append((name, mesh))
        colocated = discover_colocated_textures(path)
        mesh_texture_paths = build_mesh_texture_paths(
            named_meshes,
            glb_path=path,
            texture_by_name=colocated,
            fallback_paths=list(colocated.values()),
        ) if named_meshes else []
        attach_preview_textures(named_meshes, mesh_texture_paths)
        mesh_faces = 0
        texture_visuals = 0
        image_count = 0
        uv_sets = 0
        sampled_unique_colors = 0
        image_unique_colors = 0
        flat_images = 0
        score = 0

        for mesh in meshes:
            if not hasattr(mesh, "faces") or not hasattr(mesh, "vertices"):
                continue
            faces = np.asarray(getattr(mesh, "faces", []), dtype=int)
            verts = np.asarray(getattr(mesh, "vertices", []), dtype=float)
            mesh_faces += int(len(faces))
            visual = getattr(mesh, "visual", None)
            if visual is None:
                continue
            if getattr(visual, "kind", None) == "texture":
                texture_visuals += 1
                uv = getattr(visual, "uv", None)
                material = getattr(visual, "material", None)
                image = getattr(material, "image", None) if material is not None else None
                if uv is not None:
                    uv_sets += 1
                    score += 8
                if image is not None:
                    image_count += 1
                    try:
                        if hasattr(image, "convert"):
                            image = image.convert("RGBA")
                        img = np.asarray(image, dtype=np.uint8)
                        if img.ndim == 3 and img.shape[0] and img.shape[1]:
                            # Measure actual image variation on a bounded sample.
                            flat = img.reshape(-1, img.shape[-1])[:, :4]
                            if len(flat) > 2048:
                                flat = flat[:: max(1, len(flat) // 2048)]
                            unique_img = len(np.unique((flat[:, :3] // 8).astype(np.uint8), axis=0))
                            image_unique_colors = max(image_unique_colors, int(unique_img))
                            if unique_img <= 4:
                                flat_images += 1
                            score += 15 if unique_img > 4 else 3

                            # Measure what the model would visibly sample through UVs.
                            uv_arr = np.asarray(uv, dtype=float) if uv is not None else None
                            if uv_arr is not None and uv_arr.ndim == 2 and uv_arr.shape[1] >= 2 and len(faces):
                                sample_uv = None
                                if len(uv_arr) == len(verts):
                                    sample_uv = uv_arr[faces.reshape(-1), :2]
                                elif len(uv_arr) == len(faces) * 3:
                                    sample_uv = uv_arr[:, :2]
                                if sample_uv is not None and len(sample_uv):
                                    if len(sample_uv) > 4096:
                                        sample_uv = sample_uv[:: max(1, len(sample_uv) // 4096)]
                                    u = np.mod(sample_uv[:, 0], 1.0)
                                    v = np.mod(sample_uv[:, 1], 1.0)
                                    px = np.clip(np.rint(u * (img.shape[1] - 1)).astype(int), 0, img.shape[1] - 1)
                                    py = np.clip(np.rint((1.0 - v) * (img.shape[0] - 1)).astype(int), 0, img.shape[0] - 1)
                                    sampled = img[py, px, :3]
                                    unique_sampled = len(np.unique((sampled // 8).astype(np.uint8), axis=0))
                                    sampled_unique_colors = max(sampled_unique_colors, int(unique_sampled))
                                    score += 35 if unique_sampled >= 8 else (10 if unique_sampled >= 3 else 0)
                    except Exception:
                        score += 2
            elif getattr(visual, "vertex_colors", None) is not None:
                score += 4

        return TextureQuality(
            score=int(score),
            mesh_faces=int(mesh_faces),
            texture_visuals=int(texture_visuals),
            image_count=int(image_count),
            uv_sets=int(uv_sets),
            sampled_unique_colors=int(sampled_unique_colors),
            image_unique_colors=int(image_unique_colors),
            flat_images=int(flat_images),
        )
    except Exception:
        return TextureQuality(0, 0, 0, 0, 0, 0, 0, 0)


def converted_texture_score(path: Path) -> int:
    """Compatibility wrapper used by older UI/status code."""
    return converted_texture_quality(path).score

def converted_mesh_score(path: Path) -> int:
    """Return a rough geometry score so camera-only GLBs do not get previewed."""
    try:
        import trimesh
        loaded = trimesh.load(path, force="scene")
        meshes = loaded.dump() if isinstance(loaded, trimesh.Scene) else [loaded]
        return sum(int(len(getattr(mesh, "faces", []))) for mesh in meshes if hasattr(mesh, "faces"))
    except Exception:
        return 0


def best_preview_path(paths: list[Path]) -> Path | None:
    """Pick the converted file that is most likely to be visible in RAE.

    apicula can emit helper/camera GLBs alongside real geometry. Older RAE builds
    previewed the first file, which could show errors like camera3.glb having no
    mesh. Prefer files with faces, then files with texture data, then the first
    output as a last resort.
    """
    if not paths:
        return None
    ranked = sorted(
        paths,
        key=lambda path: (converted_mesh_score(path), converted_texture_score(path), -len(path.name)),
        reverse=True,
    )
    return ranked[0]
