"""GLB/GL mesh preview logic for PreviewWidget."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QBrush

from ...exporter import apicula_available, apicula_help_text
from ...glb_preview_textures import (
    MaterialPreviewState,
    apply_material_preview_alpha,
    attach_preview_textures,
    build_mesh_texture_paths,
    build_mesh_texture_paths_for_glb_parts,
    discover_colocated_textures,
    merge_texture_by_name,
    merge_texture_paths,
    ordered_texture_paths_from_glb,
    parse_glb_material_preview_states,
    parse_glb_material_texture_map,
    parse_glb_mesh_parts,
)
from ...nitro_textures import decode_btx_images
from .colors import qcolor_rgbf
from .preview_gl import preview_gl_options
from .preview_patch import PreviewGlbPatcher
from .textured_mesh_item import GLTexturedMeshItem

# Vertex-color baking fallback (only used when GPU texturing is unavailable).
_PREVIEW_MAX_TEXELS_PER_FACE = 12_000
_PREVIEW_MAX_TEXELS_PER_MESH = 400_000


class GlbPreviewMixin:
    def load_glb(
        self,
        path: Path,
        *,
        fallback_textures: list[Path] | None = None,
        texture_by_name: dict[str, Path] | None = None,
        material_to_texture: dict[str, str] | None = None,
        texture_bind_order: list[str] | None = None,
        mesh_texture_overrides: dict[str, Path] | None = None,
    ) -> None:
        self._last_path = path
        if fallback_textures is not None:
            self._fallback_texture_paths = list(fallback_textures)
            self._fallback_texture_image = None
            self._fallback_texture_images = {}
            self._fallback_texture_path_order = []
        if texture_by_name is not None:
            self._texture_by_name = dict(texture_by_name)
            self._fallback_texture_images = {}
            self._fallback_texture_path_order = []
        if material_to_texture is not None:
            self._material_to_texture = dict(material_to_texture)
            self._fallback_texture_images = {}
            self._fallback_texture_path_order = []
        if texture_bind_order is not None:
            self._texture_bind_order = list(texture_bind_order)
            self._fallback_texture_images = {}
            self._fallback_texture_path_order = []
        self._mesh_texture_paths: list[Path | None] = []
        self._mesh_texture_overrides = dict(mesh_texture_overrides or {})
        self._last_mesh_labels: list[str] = []
        self._preview_load_id = int(getattr(self, "_preview_load_id", 0)) + 1
        load_id = self._preview_load_id
        self._clear_meshes()
        web_view = getattr(self, "_web_view", None)
        if web_view is not None and web_view.is_available():
            self._message_label.hide()
            self._image_label.hide()
            web_view.show()
            web_view.set_background_name(getattr(self, "_background_name", "Checkered"))
            web_view.set_wireframe(getattr(self, "_wireframe", False))
            colocated = discover_colocated_textures(path)
            glb_material_map = parse_glb_material_texture_map(path)
            self._material_preview_states = parse_glb_material_preview_states(path)
            if colocated or glb_material_map:
                self._texture_by_name = merge_texture_by_name(
                    getattr(self, "_texture_by_name", {}),
                    colocated,
                    glb_material_map,
                )
                self._fallback_texture_paths = merge_texture_paths(
                    ordered_texture_paths_from_glb(path, colocated),
                    self._fallback_texture_paths,
                )
            parts = parse_glb_mesh_parts(path)
            self._last_mesh_labels = [part.label for part in parts]
            self._mesh_texture_paths = build_mesh_texture_paths_for_glb_parts(
                parts,
                glb_path=path,
                texture_by_name=getattr(self, "_texture_by_name", {}),
                material_to_texture=getattr(self, "_material_to_texture", {}),
                texture_bind_order=getattr(self, "_texture_bind_order", []),
                fallback_paths=self._fallback_texture_paths,
                mesh_texture_overrides=self._mesh_texture_overrides,
            )
            patched = self._build_web_preview_glb(path)
            web_view.load_glb(patched or path)
            self.set_banner("")
            return
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
            named_meshes = self._extract_named_meshes(loaded, trimesh)
            if not named_meshes:
                self._view.hide()
                self.set_banner(f"Converted file loaded, but no mesh faces were found: {path.name}")
                self._refresh_background_tiles()
                return

            colocated = discover_colocated_textures(path)
            glb_material_map = parse_glb_material_texture_map(path)
            self._material_preview_states = parse_glb_material_preview_states(path)
            if colocated or glb_material_map:
                self._texture_by_name = merge_texture_by_name(
                    getattr(self, "_texture_by_name", {}),
                    colocated,
                    glb_material_map,
                )
                self._fallback_texture_paths = merge_texture_paths(
                    ordered_texture_paths_from_glb(path, colocated),
                    self._fallback_texture_paths,
                )

            self._last_mesh_labels = [name for name, _mesh in named_meshes]
            self._mesh_texture_paths = build_mesh_texture_paths(
                named_meshes,
                glb_path=path,
                texture_by_name=getattr(self, "_texture_by_name", {}),
                material_to_texture=getattr(self, "_material_to_texture", {}),
                texture_bind_order=getattr(self, "_texture_bind_order", []),
                fallback_paths=self._fallback_texture_paths,
                mesh_texture_overrides=self._mesh_texture_overrides,
            )
            attached = attach_preview_textures(named_meshes, self._mesh_texture_paths)
            self._preload_texture_images(self._mesh_texture_paths)
            meshes_with_uv = sum(
                1
                for _n, mesh in named_meshes
                if getattr(getattr(mesh, "visual", None), "uv", None) is not None
            )

            # Rotate first, then center/scale the whole scene as one object.
            rotated_vertices = []
            for _name, mesh in named_meshes:
                vertices = np.asarray(mesh.vertices, dtype=float)
                vertices = self._apply_preview_orientation(vertices, np)
                rotated_vertices.append(vertices)

            all_vertices = np.vstack(rotated_vertices)
            center = all_vertices.mean(axis=0)
            extent = float(np.max(np.ptp(all_vertices - center, axis=0))) or 1.0
            scale = 40.0 / extent

            if load_id != self._preview_load_id:
                return
            textured_meshes = 0
            colored_meshes = 0
            mesh_items: list[tuple[object, str, np.ndarray]] = []
            for mesh_index, ((geom_name, mesh), vertices) in enumerate(zip(named_meshes, rotated_vertices)):
                if len(mesh.faces) == 0:
                    continue
                vertices = (vertices - center) * scale
                faces = np.asarray(mesh.faces, dtype=int)

                material_state = self._material_preview_state(mesh, geometry_name=geom_name)
                textured = (
                    self._build_textured_preview_mesh(
                        mesh,
                        vertices,
                        faces,
                        np,
                        mesh_index=mesh_index,
                        geometry_name=geom_name,
                        material_state=material_state,
                    )
                    if self._use_textures
                    else None
                )
                if textured is not None:
                    textured_item, blend_mode, mesh_center = textured
                    mesh_items.append((textured_item, blend_mode, mesh_center))
                    textured_meshes += 1
                    continue

                texture_display = (
                    self._texture_baked_display_geometry(
                        mesh,
                        vertices,
                        faces,
                        np,
                        mesh_index=mesh_index,
                        geometry_name=geom_name,
                    )
                    if self._use_textures
                    else None
                )
                if texture_display is not None:
                    display_vertices, display_faces, colors = texture_display
                    colors = apply_material_preview_alpha(colors, material_state)
                    face_colors = None
                    textured_meshes += 1
                else:
                    display_vertices = vertices
                    display_faces = faces
                    colors = (
                        self._mesh_vertex_colors(mesh, np, mesh_index=mesh_index, geometry_name=geom_name)
                        if self._use_textures
                        else None
                    )
                    colors = apply_material_preview_alpha(colors, material_state)
                    face_colors = (
                        None
                        if colors is not None
                        else self._mesh_face_colors(mesh, np, mesh_index=mesh_index, geometry_name=geom_name)
                    )
                    face_colors = apply_material_preview_alpha(face_colors, material_state)

                has_colors = colors is not None or face_colors is not None
                preview_image = self._visual_image(
                    getattr(mesh, "visual", None),
                    mesh=mesh,
                    mesh_index=mesh_index,
                    geometry_name=geom_name,
                )
                blend_mode = self._mesh_preview_blend_mode(
                    material_state,
                    preview_image,
                    colors,
                    face_colors,
                    np,
                    geometry_name=geom_name,
                    mesh=mesh,
                )
                effective_state = self._effective_preview_material_state(material_state, blend_mode)
                kwargs = dict(
                    vertexes=display_vertices,
                    faces=display_faces,
                    drawFaces=True,
                    drawEdges=self._wireframe,
                    smooth=False,
                    glOptions=preview_gl_options(blend_mode, effective_state),
                )
                if has_colors:
                    # pyqtgraph GLMeshItem requires a shader when using vertex/face colors.
                    kwargs["shader"] = "shaded"
                else:
                    kwargs["shader"] = "shaded"
                    kwargs["color"] = qcolor_rgbf("#808080")
                if colors is not None:
                    kwargs["vertexColors"] = colors
                    if texture_display is None:
                        colored_meshes += 1
                elif face_colors is not None:
                    kwargs["faceColors"] = face_colors
                    colored_meshes += 1

                item = gl.GLMeshItem(**kwargs)
                mesh_center = np.mean(display_vertices, axis=0)
                mesh_items.append((item, blend_mode, mesh_center))

            if load_id != self._preview_load_id:
                return
            ordered = self._order_preview_mesh_items(mesh_items, np)
            blend_layer = 1
            for item, blend_mode, _center in ordered:
                if blend_mode == "blend":
                    item.setDepthValue(blend_layer)
                    blend_layer += 1
                else:
                    item.setDepthValue(0)
                self._view.addItem(item)
                self._mesh_items.append(item)

            self._view.setCameraPosition(distance=self._model_distance)
            self._view.show()
            self._view.update()
            self._refresh_background_tiles()
            png_count = len(colocated) + len(self._fallback_texture_paths)
            warning = ""
            if self._use_textures and not textured_meshes and not colored_meshes:
                if png_count:
                    warning = (
                        f"Found {png_count} texture PNG(s) and {meshes_with_uv} UV mesh(es), "
                        "but preview baking produced no colors. Check Details → texture report."
                    )
                else:
                    warning = "No texture PNGs beside this GLB — run Set Textures or check apicula output."
            elif not self._use_textures:
                warning = "Textures hidden — enable Preview → Textures to show decoded colors."
            elif attached and textured_meshes == 0 and meshes_with_uv:
                warning = (
                    f"Attached {attached} texture(s) to {meshes_with_uv} UV mesh(es), "
                    "but UV layout did not match for baking."
                )
            self.set_banner(warning)
        except Exception as exc:
            self.set_banner(f"Could not preview {path.name}: {exc}")

    def refresh_web_texture_paths(self) -> None:
        """Recompute mesh→PNG paths from GLB data and current overrides."""
        path = getattr(self, "_last_path", None)
        if path is None:
            return
        parts = parse_glb_mesh_parts(path)
        self._last_mesh_labels = [part.label for part in parts]
        self._mesh_texture_paths = build_mesh_texture_paths_for_glb_parts(
            parts,
            glb_path=path,
            texture_by_name=getattr(self, "_texture_by_name", {}),
            material_to_texture=getattr(self, "_material_to_texture", {}),
            texture_bind_order=getattr(self, "_texture_bind_order", []),
            fallback_paths=self._fallback_texture_paths,
            mesh_texture_overrides=getattr(self, "_mesh_texture_overrides", {}),
        )

    def _glb_patcher(self) -> PreviewGlbPatcher:
        patcher = getattr(self, "_preview_glb_patcher", None)
        if patcher is None:
            patcher = PreviewGlbPatcher()
            self._preview_glb_patcher = patcher
        return patcher

    def _build_web_preview_glb(self, source_glb: Path) -> Path | None:
        labels = list(getattr(self, "_last_mesh_labels", []))
        paths = list(getattr(self, "_mesh_texture_paths", []))
        if not labels or not any(paths):
            return None
        return self._glb_patcher().build_preview_glb(
            source_glb,
            mesh_labels=labels,
            mesh_texture_paths=paths,
        )

    def _reload_web_preview_glb(self) -> None:
        path = getattr(self, "_last_path", None)
        web_view = getattr(self, "_web_view", None)
        if path is None or web_view is None or not web_view.is_available():
            return
        web_view.pause_flipbook()
        patched = self._build_web_preview_glb(path)
        if patched is not None:
            web_view.load_glb(patched)

    def _extract_named_meshes(self, loaded, trimesh) -> list[tuple[str, object]]:
        if isinstance(loaded, trimesh.Scene):
            geom_nodes: dict[str, list[str]] = {}
            try:
                for node_name, geom_name in loaded.graph.nodes_geometry:
                    geom_nodes.setdefault(str(geom_name), []).append(str(node_name))
            except Exception:
                pass

            # dump() splits multi-material meshes the way older RAE builds expected.
            raw = loaded.dump()
            named: list[tuple[str, object]] = []
            for idx, mesh in enumerate(raw):
                if isinstance(mesh, trimesh.Trimesh) and len(mesh.vertices) and len(mesh.faces):
                    meta = getattr(mesh, "metadata", {}) or {}
                    geom_key = str(meta.get("geometry", "") or "")
                    node_names = geom_nodes.get(geom_key, [])
                    material = getattr(getattr(mesh, "visual", None), "material", None)
                    mat_name = str(getattr(material, "name", "") or "")
                    name = str(
                        mat_name
                        or meta.get("name", "")
                        or (node_names[0] if node_names else "")
                        or geom_key
                        or f"mesh_{idx}"
                    )
                    named.append((name, mesh))
            if named:
                return named
            for name, geom in loaded.geometry.items():
                if isinstance(geom, trimesh.Trimesh) and len(geom.vertices) and len(geom.faces):
                    named.append((str(name), geom))
            return named
        if isinstance(loaded, trimesh.Trimesh) and len(loaded.vertices) and len(loaded.faces):
            name = str(getattr(loaded, "metadata", {}).get("name", "") or "mesh_0")
            return [(name, loaded)]
        return []

    def _material_preview_state(self, mesh, *, geometry_name: str = "") -> MaterialPreviewState | None:
        states = getattr(self, "_material_preview_states", {})
        if not states:
            return None
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
        meta = getattr(mesh, "metadata", None)
        if isinstance(meta, dict):
            for key in ("material_id", "mat_id", "material"):
                value = meta.get(key)
                if isinstance(value, int) and str(value) in states:
                    return states[str(value)]
        return None

    def _colors_need_translucent_blend(self, colors) -> bool:
        if colors is None:
            return False
        try:
            import numpy as np

            arr = np.asarray(colors, dtype=float)
            if arr.ndim != 2 or arr.shape[1] < 4 or len(arr) == 0:
                return False
            return bool(np.any(arr[:, 3] < 0.995))
        except Exception:
            return False

    def _build_textured_preview_mesh(
        self,
        mesh,
        vertices,
        faces,
        np,
        *,
        mesh_index: int = 0,
        geometry_name: str = "",
        material_state: MaterialPreviewState | None = None,
    ):
        """GPU nearest-neighbor texturing — pixel-perfect NDS preview."""
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None
        uv = getattr(visual, "uv", None)
        if getattr(visual, "kind", None) != "texture" and uv is None:
            return None
        image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index, geometry_name=geometry_name)
        if image is None or uv is None:
            return None
        img = self._image_rgba_uint8(image, np)
        if img is None:
            return None
        expanded = self._expand_mesh_uv_draw_arrays(vertices, faces, uv, mesh, np)
        if expanded is None:
            return None
        flat_v, flat_f, flat_uv = expanded
        if len(flat_v) == 0 or len(flat_f) == 0:
            return None
        blend_mode = self._preview_blend_mode(
            material_state,
            img,
            np,
            geometry_name=geometry_name,
            mesh=mesh,
        )
        effective_state = self._effective_preview_material_state(material_state, blend_mode)
        try:
            item = GLTexturedMeshItem(
                flat_v,
                flat_f,
                flat_uv,
                img,
                material_state=effective_state,
                blend_mode=blend_mode,
                draw_edges=self._wireframe,
            )
        except Exception:
            return None
        mesh_center = np.mean(flat_v, axis=0)
        return item, blend_mode, mesh_center

    def _expand_mesh_uv_draw_arrays(self, vertices, faces, uv, mesh, np):
        """Duplicate face corners so each triangle has its own UV vertices for the GPU path."""
        source_faces = np.asarray(mesh.faces, dtype=int)
        uv_arr = np.asarray(uv, dtype=float)
        if uv_arr.ndim != 2 or uv_arr.shape[1] < 2 or len(source_faces) == 0:
            return None

        if len(uv_arr) == len(mesh.vertices):
            flat_v = vertices[source_faces.reshape(-1)]
            flat_uv = uv_arr[source_faces.reshape(-1), :2].copy()
        elif len(uv_arr) == len(source_faces) * 3:
            flat_v = vertices[source_faces.reshape(-1)]
            flat_uv = uv_arr[:, :2].copy()
        else:
            corner_count = int(source_faces.size)
            flat_v = vertices[source_faces.reshape(-1)]
            if len(uv_arr) == corner_count:
                flat_uv = uv_arr[:, :2].copy()
            elif len(uv_arr) == len(faces) * 3:
                flat_uv = uv_arr[:, :2].copy()
            else:
                flat_uv = np.resize(uv_arr[:, :2], (corner_count, 2))

        flat_uv[:, 0] = np.mod(flat_uv[:, 0], 1.0)
        flat_uv[:, 1] = 1.0 - np.mod(flat_uv[:, 1], 1.0)
        flat_f = np.arange(len(flat_v), dtype=np.uint32).reshape(-1, 3)
        return flat_v.astype(np.float32), flat_f, flat_uv.astype(np.float32)

    def _is_uniform_decal_material(self, material_state: MaterialPreviewState | None) -> bool:
        state = material_state or MaterialPreviewState()
        return state.render_class == "uniform_decal"

    def _preview_blend_mode(
        self,
        material_state: MaterialPreviewState | None,
        img,
        np,
        *,
        geometry_name: str = "",
        mesh=None,
    ) -> str:
        """Classify how a textured mesh should participate in the depth/blend passes."""
        state = material_state or MaterialPreviewState()
        has_cutout = self._texture_has_cutout_alpha(img, np)
        has_partial = self._texture_has_partial_alpha(img, np)
        shadow_decal = self._is_uniform_decal_material(material_state)

        # glTF MASK is authoritative — never upgrade to blend because PNG export
        # added fringe pixels around DS 0/1 cutout texels.
        if state.alpha_mode == "MASK":
            return "cutout"

        if state.alpha_mode == "BLEND":
            # apicula marks some binary-cutout DS textures BLEND when the Nitro
            # format is "translucent"; keep depth writes when alpha is only 0/255.
            if has_cutout and not has_partial:
                return "cutout"
            if has_partial:
                return "blend"
            if shadow_decal:
                return "shadow"
            return "opaque"

        if shadow_decal:
            return "shadow"
        if has_cutout:
            return "cutout"
        return "opaque"

    def _effective_preview_material_state(
        self,
        material_state: MaterialPreviewState | None,
        blend_mode: str,
    ) -> MaterialPreviewState:
        """Ensure cutout draw passes enable shader alpha discard."""
        state = material_state or MaterialPreviewState()
        if blend_mode != "cutout" or state.alpha_mode == "MASK":
            return state
        return MaterialPreviewState(
            alpha=state.alpha,
            alpha_mode="MASK",
            alpha_cutoff=state.alpha_cutoff,
            double_sided=state.double_sided,
        )

    def _texture_has_cutout_alpha(self, img, np) -> bool:
        try:
            alpha = np.asarray(img, dtype=np.uint8)[:, :, 3]
            return bool(np.any(alpha < 250))
        except Exception:
            return False

    def _texture_has_partial_alpha(self, img, np) -> bool:
        try:
            alpha = np.asarray(img, dtype=np.uint8)[:, :, 3]
            partial = alpha[(alpha > 8) & (alpha < 247)]
            return partial.size > 0
        except Exception:
            return False

    def _mesh_preview_blend_mode(
        self,
        material_state: MaterialPreviewState | None,
        image,
        colors,
        face_colors,
        np,
        *,
        geometry_name: str = "",
        mesh=None,
    ) -> str:
        """Pick opaque/cutout/blend using glTF material state and texture data."""
        if image is not None:
            img = self._image_rgba_uint8(image, np)
            if img is not None:
                return self._preview_blend_mode(
                    material_state,
                    img,
                    np,
                    geometry_name=geometry_name,
                    mesh=mesh,
                )
        state = material_state or MaterialPreviewState()
        shadow_decal = self._is_uniform_decal_material(material_state)
        if state.alpha_mode == "MASK":
            return "cutout"
        if state.alpha_mode == "BLEND":
            if self._colors_need_true_blend(colors) or self._colors_need_true_blend(face_colors):
                return "blend"
            if shadow_decal:
                return "shadow"
            if self._colors_need_translucent_blend(colors) or self._colors_need_translucent_blend(face_colors):
                return "cutout"
            return "opaque"
        if shadow_decal:
            return "shadow"
        if self._colors_need_true_blend(colors) or self._colors_need_true_blend(face_colors):
            return "blend"
        if self._colors_need_translucent_blend(colors) or self._colors_need_translucent_blend(face_colors):
            return "cutout"
        return "opaque"

    def _colors_need_true_blend(self, colors) -> bool:
        if colors is None:
            return False
        try:
            import numpy as np

            arr = np.asarray(colors, dtype=float)
            if arr.ndim != 2 or arr.shape[1] < 4 or len(arr) == 0:
                return False
            alpha = arr[:, 3]
            partial = alpha[(alpha > 0.03) & (alpha < 0.97)]
            return partial.size > 0
        except Exception:
            return False

    def _preview_camera_eye(self, np):
        view = self._view
        if view is None:
            return np.zeros(3, dtype=float)
        try:
            opts = getattr(view, "opts", {})
            center = np.asarray(opts.get("center", (0.0, 0.0, 0.0)), dtype=float).reshape(-1)[:3]
            distance = float(opts.get("distance", self._model_distance) or self._model_distance)
            elev = np.radians(float(opts.get("elevation", 0.0) or 0.0))
            azim = np.radians(float(opts.get("azimuth", 0.0) or 0.0))
            return center + distance * np.array(
                (
                    np.cos(elev) * np.sin(azim),
                    np.cos(elev) * np.cos(azim),
                    np.sin(elev),
                ),
                dtype=float,
            )
        except Exception:
            return np.zeros(3, dtype=float)

    def _order_preview_mesh_items(self, mesh_items, np):
        """Opaque/cutout near-to-far, then alpha-blended meshes far-to-near."""
        shadows = [entry for entry in mesh_items if entry[1] == "shadow"]
        solid = [entry for entry in mesh_items if entry[1] not in {"blend", "shadow"}]
        blended = [entry for entry in mesh_items if entry[1] == "blend"]
        eye = self._preview_camera_eye(np)

        def camera_distance(entry) -> float:
            center = np.asarray(entry[2], dtype=float).reshape(-1)[:3]
            return float(np.linalg.norm(center - eye))

        solid.sort(key=camera_distance)
        blended.sort(key=camera_distance, reverse=True)
        # Ground shadows first (depth-writing decal), then opaque/cutout, then true blend.
        return shadows + solid + blended

    def _apply_preview_orientation(self, vertices, np):
        # Pokémon B2W2/apicula preview fix: the converted model's +Y axis is the
        # vertical axis we want to show as +Z in RAE's preview. This is preview-only;
        # exported GLB/DAE files are left exactly as apicula writes them.
        x = vertices[:, 0].copy()
        y = vertices[:, 1].copy()
        z = vertices[:, 2].copy()
        return np.column_stack((x, z, y))

    def _texture_baked_display_geometry(
        self,
        mesh,
        vertices,
        faces,
        np,
        *,
        mesh_index: int = 0,
        geometry_name: str = "",
    ):
        """Return preview geometry with pixel-art textures baked as solid texel quads.

        GLMeshItem cannot sample textures in the GPU. Corner vertex colors get
        Gouraud-interpolated across each triangle, which smears NDS pixel art.
        Instead, rasterize covered texels in UV space and emit one unlit quad per
        texel with identical colors on every corner.
        """
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None
        uv = getattr(visual, "uv", None)
        if getattr(visual, "kind", None) != "texture" and uv is None:
            return None
        image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index, geometry_name=geometry_name)
        if image is None or uv is None:
            return None
        uv_arr = np.asarray(uv, dtype=float)
        if uv_arr.ndim != 2 or uv_arr.shape[1] < 2 or len(faces) == 0:
            return None

        try:
            img = self._image_rgba_uint8(image, np)
            if img is None:
                return None
            height, width = int(img.shape[0]), int(img.shape[1])
            if width <= 0 or height <= 0:
                return None

            face_uvs = self._face_uv_corners(mesh, faces, uv_arr, np)
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
                    corner_colors = self._sample_image_array_at_px(tri_px, img, np)
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
                        weights = self._barycentric_weights_2d(center, tri_px)
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
                            corner_w = self._barycentric_weights_2d(px_corner, tri_px)
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

    def _face_uv_corners(self, mesh, faces, uv_arr, np):
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
        flat_uv = np.resize(uv_arr[:, :2], (corner_count, 2))
        return flat_uv.reshape(len(source_faces), 3, 2)

    def _barycentric_weights_2d(self, point, triangle_px):
        try:
            a, b, c = triangle_px
            v0 = c - a
            v1 = b - a
            v2 = point - a
            dot00 = float(v0 @ v0)
            dot01 = float(v0 @ v1)
            dot02 = float(v0 @ v2)
            dot11 = float(v1 @ v1)
            dot12 = float(v1 @ v2)
            denom = dot00 * dot11 - dot01 * dot01
            if abs(denom) < 1e-12:
                return None
            inv = 1.0 / denom
            u = (dot11 * dot02 - dot01 * dot12) * inv
            v = (dot00 * dot12 - dot01 * dot02) * inv
            return np.array((1.0 - u - v, v, u), dtype=float)
        except Exception:
            return None

    def _image_rgba_uint8(self, image, np):
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

    def _sample_image_array_at_px(self, px_coords, img, np):
        try:
            height, width = int(img.shape[0]), int(img.shape[1])
            px = np.clip(np.rint(px_coords[:, 0]).astype(int), 0, width - 1)
            py = np.clip(np.rint(px_coords[:, 1]).astype(int), 0, height - 1)
            return img[py, px, :4].astype(float) / 255.0
        except Exception:
            return None

    def _mesh_vertex_colors(self, mesh, np, *, mesh_index: int = 0, geometry_name: str = ""):
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None

        # Best case: apicula/trimesh gave us UVs plus an image. Sample the image
        # to vertex colors because GLMeshItem does not support UV texture maps.
        if getattr(visual, "kind", None) == "texture":
            uv = getattr(visual, "uv", None)
            image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index, geometry_name=geometry_name)
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

        if getattr(visual, "kind", None) != "texture":
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

    def _mesh_face_colors(self, mesh, np, *, mesh_index: int = 0, geometry_name: str = ""):
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None

        # More robust texture preview: some glTF/DAE imports keep UVs per face
        # corner instead of per vertex. GLMeshItem cannot render UV textures, but
        # it can render face colors, so average each triangle's sampled texels.
        if getattr(visual, "kind", None) == "texture":
            uv = getattr(visual, "uv", None)
            image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index, geometry_name=geometry_name)
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

    def _visual_image(self, visual, *, mesh=None, mesh_index: int = 0, geometry_name: str = ""):
        material = getattr(visual, "material", None)
        image = getattr(material, "image", None) if material is not None else None
        if image is not None:
            return image

        mesh_paths = getattr(self, "_mesh_texture_paths", [])
        if mesh_index < len(mesh_paths) and mesh_paths[mesh_index] is not None:
            return self._image_for_path(mesh_paths[mesh_index])

        self._ensure_fallback_texture_cache()
        paths = getattr(self, "_fallback_texture_path_order", [])
        if paths:
            return self._image_for_path(paths[mesh_index % len(paths)])
        return None

    def _image_for_path(self, path: Path | None):
        if path is None:
            return None
        for key in (str(path), str(path.resolve()) if path.exists() else ""):
            if key and key in self._fallback_texture_images:
                return self._fallback_texture_images[key]
        try:
            from PIL import Image as PILImage

            image = PILImage.open(path).convert("RGBA")
        except Exception:
            return None
        self._index_texture_image(path, image)
        return image

    def _preload_texture_images(self, paths: list[Path | None]) -> None:
        for path in paths:
            if path is not None:
                self._image_for_path(path)
        for path in getattr(self, "_texture_by_name", {}).values():
            self._image_for_path(path)

    def _image_pixel_area(self, image) -> int:
        if hasattr(image, "size"):
            w, h = image.size
            return int(w) * int(h)
        try:
            import numpy as np

            arr = np.asarray(image)
            if arr.ndim >= 2:
                return int(arr.shape[0]) * int(arr.shape[1])
        except Exception:
            pass
        return 0

    def _set_cached_image(self, key: str, image) -> None:
        if not key:
            return
        current = self._fallback_texture_images.get(key)
        if current is None or self._image_pixel_area(image) > self._image_pixel_area(current):
            self._fallback_texture_images[key] = image

    def _index_texture_image(self, path: Path, image) -> None:
        self._set_cached_image(str(path), image)
        try:
            self._set_cached_image(str(path.resolve()), image)
        except Exception:
            pass
        stem = path.stem.casefold()
        self._set_cached_image(stem, image)
        base = stem.split("__", 1)[0]
        self._set_cached_image(base, image)

    def _ensure_fallback_texture_cache(self) -> None:
        if self._fallback_texture_images:
            return
        ordered: list[Path] = []
        seen: set[str] = set()
        for path in (
            *self._fallback_texture_paths,
            *(p for p in getattr(self, "_mesh_texture_paths", []) if p is not None),
            *getattr(self, "_texture_by_name", {}).values(),
        ):
            try:
                key = str(path.resolve())
            except Exception:
                key = str(path)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(path)
            self._image_for_path(path)
        self._fallback_texture_path_order = ordered
        if ordered:
            self._fallback_texture_image = self._image_for_path(ordered[0])

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

