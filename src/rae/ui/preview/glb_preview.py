"""GLB/GL mesh preview logic for PreviewWidget."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QBrush

from ...exporter import apicula_available, apicula_help_text
from ...nitro_textures import decode_btx_images
from .colors import qcolor_rgbf

class GlbPreviewMixin:
    def load_glb(self, path: Path, *, fallback_textures: list[Path] | None = None) -> None:
        self._last_path = path
        if fallback_textures is not None:
            self._fallback_texture_paths = list(fallback_textures)
            self._fallback_texture_image = None
            self._fallback_texture_images = {}
            self._fallback_texture_path_order = []
        if not self._available or self._view is None:
            self.set_banner(f"Converted file ready: {path.name}")
            return

        try:
            import numpy as np
            import trimesh
            import pyqtgraph.opengl as gl
        except Exception as exc:
            self.set_banner(f"Preview dependencies unavailable: {exc}")
            return

        try:
            self._message_label.hide()
            self._image_label.hide()
            self._view.show()
            self._refresh_background_tiles()
            loaded = trimesh.load(path, force="scene")
            meshes = self._extract_meshes(loaded, trimesh)
            if not meshes:
                self._view.hide()
                self.set_banner(f"Converted file loaded, but no mesh faces were found: {path.name}")
                self._refresh_background_tiles()
                return

            # Rotate first, then center/scale the whole scene as one object.
            rotated_vertices = []
            for mesh in meshes:
                vertices = np.asarray(mesh.vertices, dtype=float)
                vertices = self._apply_preview_orientation(vertices, np)
                rotated_vertices.append(vertices)

            all_vertices = np.vstack(rotated_vertices)
            center = all_vertices.mean(axis=0)
            extent = float(np.max(np.ptp(all_vertices - center, axis=0))) or 1.0
            scale = 40.0 / extent

            self._clear_meshes()
            textured_meshes = 0
            colored_meshes = 0
            for mesh_index, (mesh, vertices) in enumerate(zip(meshes, rotated_vertices)):
                if len(mesh.faces) == 0:
                    continue
                vertices = (vertices - center) * scale
                faces = np.asarray(mesh.faces, dtype=int)

                # pyqtgraph's GLMeshItem cannot render UV-mapped textures. Older
                # RAE builds tried to fall back to the material color when the
                # imported UV array did not line up one-to-one with vertices.
                # Many GLB/DAE imports store UVs per face corner, so that fallback
                # made genuinely textured models appear gray. Bake the texture
                # into per-corner vertex colors for preview only. Exports remain
                # untouched.
                texture_display = self._texture_baked_display_geometry(mesh, vertices, faces, np, mesh_index=mesh_index) if self._use_textures else None
                if texture_display is not None:
                    display_vertices, display_faces, colors = texture_display
                    face_colors = None
                    textured_meshes += 1
                else:
                    display_vertices = vertices
                    display_faces = faces
                    colors = self._mesh_vertex_colors(mesh, np, mesh_index=mesh_index) if self._use_textures else None
                    face_colors = None if colors is not None else self._mesh_face_colors(mesh, np, mesh_index=mesh_index)

                kwargs = dict(
                    vertexes=display_vertices,
                    faces=display_faces,
                    drawFaces=True,
                    drawEdges=self._wireframe or (colors is None and face_colors is None),
                    smooth=False,
                    shader="shaded",
                )
                if colors is not None:
                    kwargs["vertexColors"] = colors
                    if texture_display is None:
                        colored_meshes += 1
                elif face_colors is not None:
                    kwargs["faceColors"] = face_colors
                    colored_meshes += 1

                item = gl.GLMeshItem(**kwargs)
                self._view.addItem(item)
                self._mesh_items.append(item)

            self._view.setCameraPosition(distance=self._model_distance)
            self._view.show()
            self._refresh_background_tiles()
            warning = ""
            if self._use_textures and not textured_meshes and not colored_meshes:
                if self._fallback_texture_paths:
                    warning = (
                        "Decoded texture PNGs are available, but this converted mesh "
                        "did not expose bindable UV texture data for preview."
                    )
                else:
                    warning = "No renderable texture data in this converted file."
            elif not self._use_textures:
                warning = "Textures hidden — enable Preview → Textures to show decoded colors."
            self.set_banner(warning)
        except Exception as exc:
            self.set_banner(f"Could not preview {path.name}: {exc}")

    def _extract_meshes(self, loaded, trimesh):
        if isinstance(loaded, trimesh.Scene):
            raw = loaded.dump()
            return [m for m in raw if isinstance(m, trimesh.Trimesh) and len(m.vertices) and len(m.faces)]
        if isinstance(loaded, trimesh.Trimesh) and len(loaded.vertices) and len(loaded.faces):
            return [loaded]
        return []

    def _apply_preview_orientation(self, vertices, np):
        # Pokémon B2W2/apicula preview fix: the converted model's +Y axis is the
        # vertical axis we want to show as +Z in RAE's preview. This is preview-only;
        # exported GLB/DAE files are left exactly as apicula writes them.
        x = vertices[:, 0].copy()
        y = vertices[:, 1].copy()
        z = vertices[:, 2].copy()
        return np.column_stack((x, z, y))

    def _texture_baked_display_geometry(self, mesh, vertices, faces, np, *, mesh_index: int = 0):
        """Return preview-only geometry with texture sampled into vertex colors.

        GLMeshItem has no UV texture stage. To make converted GLB/DAE textures
        visible inside RAE, duplicate each triangle corner and color it by the
        texel at that corner's UV. This preserves DS/glTF wrap behavior well
        enough for browsing while keeping the exported files exactly as apicula
        wrote them.
        """
        visual = getattr(mesh, "visual", None)
        if visual is None or getattr(visual, "kind", None) != "texture":
            return None
        image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index)
        uv = getattr(visual, "uv", None)
        if image is None or uv is None:
            return None
        uv_arr = np.asarray(uv, dtype=float)
        if uv_arr.ndim != 2 or uv_arr.shape[1] < 2 or len(faces) == 0:
            return None

        try:
            source_faces = np.asarray(mesh.faces, dtype=int)
            if len(uv_arr) == len(mesh.vertices):
                flat_uv = uv_arr[source_faces.reshape(-1), :2]
            elif len(uv_arr) == len(source_faces) * 3:
                flat_uv = uv_arr[:, :2]
            else:
                return None

            flat_vertices = vertices[faces.reshape(-1)]
            colors = self._sample_image_at_uv(flat_uv, image, np)
            if colors is None or len(colors) != len(flat_vertices):
                return None
            display_faces = np.arange(len(flat_vertices), dtype=int).reshape(-1, 3)
            return flat_vertices, display_faces, colors
        except Exception:
            return None

    def _mesh_vertex_colors(self, mesh, np, *, mesh_index: int = 0):
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None

        # Best case: apicula/trimesh gave us UVs plus an image. Sample the image
        # to vertex colors because GLMeshItem does not support UV texture maps.
        if getattr(visual, "kind", None) == "texture":
            uv = getattr(visual, "uv", None)
            image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index)
            if uv is not None and image is not None and len(uv) == len(mesh.vertices):
                colors = self._sample_image_at_uv(uv, image, np)
                if colors is not None:
                    return colors

            # Fallback: use the material base color only when there is no texture
            # image to sample. If an image exists but the UV layout is per-face,
            # _texture_baked_display_geometry should handle it; returning a gray
            # material here would hide the real texture.
            if image is None:
                material = getattr(visual, "material", None)
                material_color = self._material_color(material, np)
                if material_color is not None:
                    return np.tile(material_color, (len(mesh.vertices), 1))

        # Vertex-color assets can be rendered directly.
        vertex_colors = getattr(visual, "vertex_colors", None)
        if vertex_colors is not None and len(vertex_colors) == len(mesh.vertices):
            colors = np.asarray(vertex_colors, dtype=float)
            if colors.max(initial=1.0) > 1.0:
                colors = colors / 255.0
            if colors.shape[1] == 3:
                colors = np.column_stack([colors, np.ones(len(colors))])
            return colors[:, :4]
        return None

    def _mesh_face_colors(self, mesh, np, *, mesh_index: int = 0):
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None

        # More robust texture preview: some glTF/DAE imports keep UVs per face
        # corner instead of per vertex. GLMeshItem cannot render UV textures, but
        # it can render face colors, so average each triangle's sampled texels.
        if getattr(visual, "kind", None) == "texture":
            uv = getattr(visual, "uv", None)
            image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index)
            if uv is not None and image is not None:
                uv_arr = np.asarray(uv, dtype=float)
                try:
                    if len(uv_arr) == len(mesh.vertices):
                        face_uv = uv_arr[np.asarray(mesh.faces, dtype=int)].reshape(-1, 2)
                        sampled = self._sample_image_at_uv(face_uv, image, np)
                        if sampled is not None:
                            return sampled.reshape(len(mesh.faces), 3, 4).mean(axis=1)
                    if len(uv_arr) == len(mesh.faces) * 3:
                        sampled = self._sample_image_at_uv(uv_arr, image, np)
                        if sampled is not None:
                            return sampled.reshape(len(mesh.faces), 3, 4).mean(axis=1)
                except Exception:
                    pass

        face_colors = getattr(visual, "face_colors", None)
        if face_colors is None or len(face_colors) != len(mesh.faces):
            return None
        colors = np.asarray(face_colors, dtype=float)
        if colors.max(initial=1.0) > 1.0:
            colors = colors / 255.0
        if colors.shape[1] == 3:
            colors = np.column_stack([colors, np.ones(len(colors))])
        return colors[:, :4]

    def _visual_image(self, visual, *, mesh=None, mesh_index: int = 0):
        material = getattr(visual, "material", None)
        image = getattr(material, "image", None) if material is not None else None
        if image is not None:
            return image
        # Some apicula outputs keep UV/material slots but no embedded GLB image
        # even when RAE has decoded the Nitro TEX0/BTX0 PNGs correctly. Do not
        # reuse one global fallback image for every mesh part: many Pokémon map
        # props have multiple materials/textures. Prefer a fallback whose file
        # name matches the material/mesh name, then fall back to a stable
        # per-mesh round-robin so multi-texture models visibly use more than the
        # first decoded image.
        self._ensure_fallback_texture_cache()
        if not self._fallback_texture_images:
            return None

        keys = self._fallback_match_keys(visual, mesh)
        for key in keys:
            match = self._find_fallback_image_by_key(key)
            if match is not None:
                return match

        if self._fallback_texture_path_order:
            path = self._fallback_texture_path_order[mesh_index % len(self._fallback_texture_path_order)]
            return self._fallback_texture_images.get(str(path))
        return None

    def _ensure_fallback_texture_cache(self) -> None:
        if self._fallback_texture_images or not self._fallback_texture_paths:
            return
        try:
            from PIL import Image as PILImage
        except Exception:
            return
        ordered: list[Path] = []
        for path in self._fallback_texture_paths:
            try:
                img = PILImage.open(path).convert("RGBA")
            except Exception:
                continue
            ordered.append(path)
            self._fallback_texture_images[str(path)] = img
            stem = path.stem.casefold()
            self._fallback_texture_images[stem] = img
            # Decoded variants often include palette names or numeric suffixes;
            # index useful prefixes too, e.g. gym02_001tga__pal and
            # resolved_texture_00_gym02_001tga.
            for token in re.split(r"[^A-Za-z0-9_]+", path.stem):
                token = token.strip("_").casefold()
                if len(token) >= 3 and token not in self._fallback_texture_images:
                    self._fallback_texture_images[token] = img
            for part in path.stem.split("__"):
                part = part.strip("_").casefold()
                if len(part) >= 3 and part not in self._fallback_texture_images:
                    self._fallback_texture_images[part] = img
        self._fallback_texture_path_order = ordered
        if ordered:
            self._fallback_texture_image = self._fallback_texture_images.get(str(ordered[0]))

    def _fallback_match_keys(self, visual, mesh=None) -> list[str]:
        keys: list[str] = []
        material = getattr(visual, "material", None)
        for obj in (material, visual, mesh):
            if obj is None:
                continue
            for attr in ("name", "image_name", "file_name"):
                value = getattr(obj, attr, None)
                if isinstance(value, str) and value:
                    keys.append(value)
            meta = getattr(obj, "metadata", None)
            if isinstance(meta, dict):
                for value in meta.values():
                    if isinstance(value, str) and value:
                        keys.append(value)
        out: list[str] = []
        seen: set[str] = set()
        for key in keys:
            base = Path(str(key)).stem
            variants = [base, base.replace(".tga", ""), base.replace("_pl", "")]
            for item in variants:
                norm = item.strip("_").casefold()
                if len(norm) >= 3 and norm not in seen:
                    seen.add(norm)
                    out.append(norm)
        return out

    def _find_fallback_image_by_key(self, key: str):
        key = key.casefold().strip("_")
        if not key:
            return None
        direct = self._fallback_texture_images.get(key)
        if direct is not None:
            return direct
        for stored_key, image in self._fallback_texture_images.items():
            if len(stored_key) < 3:
                continue
            if key in stored_key or stored_key in key:
                return image
        return None

    def _sample_image_at_uv(self, uv, image, np):
        try:
            if hasattr(image, "convert"):
                image = image.convert("RGBA")
            img = np.asarray(image, dtype=np.uint8)
            if img.ndim != 3 or img.shape[0] <= 0 or img.shape[1] <= 0:
                return None
            if img.shape[2] == 3:
                alpha = np.full((img.shape[0], img.shape[1], 1), 255, dtype=np.uint8)
                img = np.concatenate([img, alpha], axis=2)
            uv_arr = np.asarray(uv, dtype=float)
            if uv_arr.ndim != 2 or uv_arr.shape[1] < 2:
                return None
            u = np.mod(uv_arr[:, 0], 1.0)
            v = np.mod(uv_arr[:, 1], 1.0)
            px = np.clip(np.rint(u * (img.shape[1] - 1)).astype(int), 0, img.shape[1] - 1)
            py = np.clip(np.rint((1.0 - v) * (img.shape[0] - 1)).astype(int), 0, img.shape[0] - 1)
            return img[py, px, :4].astype(float) / 255.0
        except Exception:
            return None

    def _material_color(self, material, np):
        if material is None:
            return None
        for attr in ("baseColorFactor", "diffuse"):
            value = getattr(material, attr, None)
            if value is None:
                continue
            arr = np.asarray(value, dtype=float).reshape(-1)
            if arr.size >= 3:
                if arr.max(initial=1.0) > 1.0:
                    arr = arr / 255.0
                if arr.size == 3:
                    arr = np.concatenate([arr, [1.0]])
                return arr[:4]
        return None

