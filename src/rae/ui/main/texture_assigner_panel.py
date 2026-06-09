"""Wire the coloring-book texture assigner into the main preview panel."""
from __future__ import annotations

from pathlib import Path

from ...core.texture_assignments import (
    build_best_path_index,
    estimate_assignments_from_paths,
    image_pixel_area,
    relevant_assigner_texture_paths,
    resolve_assignment_paths,
    texture_key_for_path,
)
from ...scanner import Asset
from ..preview.texture_assigner import TextureAssignerWidget, TextureOption


class TextureAssignerPanelMixin:
    def _init_texture_assigner_state(self) -> None:
        self._texture_assignments: dict[str, dict[str, str]] = {}
        self._preview_mesh_labels: list[str] = []
        self._last_inspector_asset_id: str | None = None

    def _mesh_texture_overrides_for_asset(self, asset_id: str | None) -> dict[str, Path]:
        if not asset_id:
            return {}
        stored = self._texture_assignments.get(asset_id)
        if not stored:
            return {}
        preview = self.preview
        return resolve_assignment_paths(
            stored,
            texture_by_name=getattr(preview, "_texture_by_name", {}),
            fallback_paths=getattr(preview, "_fallback_texture_paths", []),
        )

    def _texture_options_for_preview(self, asset_id: str | None = None) -> list[TextureOption]:
        preview = self.preview
        asset_id = asset_id or getattr(self, "_last_previewed_asset_id", None)
        assignments = dict(self._texture_assignments.get(asset_id, {})) if asset_id else {}
        paths = relevant_assigner_texture_paths(
            fallback_paths=list(getattr(preview, "_fallback_texture_paths", [])),
            texture_by_name=getattr(preview, "_texture_by_name", {}),
            mesh_texture_paths=list(getattr(preview, "_mesh_texture_paths", [])),
            material_to_texture=getattr(preview, "_material_to_texture", {}),
            assignments=assignments,
            glb_path=getattr(preview, "_last_path", None),
            mesh_part_labels=list(getattr(self, "_preview_mesh_labels", [])),
        )
        best = build_best_path_index(paths)
        options: list[TextureOption] = []
        seen: set[str] = set()
        for key, path in sorted(best.items(), key=lambda item: (-image_pixel_area(item[1]), item[0])):
            base = texture_key_for_path(path)
            if base in seen:
                continue
            seen.add(base)
            w, h = 0, 0
            try:
                from PIL import Image

                with Image.open(path) as img:
                    w, h = img.size
            except Exception:
                pass
            label = f"{base} ({w}×{h})" if w and h else base
            options.append(TextureOption(key=base, label=label, path=path))
        return options

    def _refresh_texture_assigner(self, asset: Asset | None = None) -> None:
        if not hasattr(self, "texture_assigner"):
            return
        asset = asset or self.selected_asset()
        if not asset or asset.magic != "BMD0":
            self.texture_assigner.set_context(asset_id=None, parts=[], textures=[], assignments={})
            return
        parts = list(getattr(self, "_preview_mesh_labels", []))
        textures = self._texture_options_for_preview()
        assignments = dict(self._texture_assignments.get(asset.asset_id, {}))
        self.texture_assigner.set_context(
            asset_id=asset.asset_id,
            parts=parts,
            textures=textures,
            assignments=assignments,
        )

    def _on_texture_assignments_changed(self, assignments: dict[str, str]) -> None:
        asset = self.selected_asset()
        if not asset:
            return
        if assignments:
            self._texture_assignments[asset.asset_id] = dict(assignments)
        else:
            self._texture_assignments.pop(asset.asset_id, None)
        self._reload_preview_with_assignments()

    def _reload_preview_with_assignments(self) -> None:
        preview = self.preview
        path = getattr(preview, "_last_path", None)
        if path is None:
            return
        overrides = self._mesh_texture_overrides_for_asset(
            getattr(self, "_last_previewed_asset_id", None) or (self.selected_asset().asset_id if self.selected_asset() else None)
        )
        preview.load_glb(
            path,
            fallback_textures=preview._fallback_texture_paths,
            texture_by_name=preview._texture_by_name,
            material_to_texture=preview._material_to_texture,
            texture_bind_order=preview._texture_bind_order,
            mesh_texture_overrides=overrides,
        )
        self._refresh_texture_assigner()

    def _store_preview_mesh_labels(self, labels: list[str]) -> None:
        self._preview_mesh_labels = list(labels)

    def _model_preview_is_active(self, asset: Asset | None = None) -> bool:
        asset = asset or self.selected_asset()
        if asset is None or asset.magic != "BMD0":
            return False
        if getattr(self, "_last_previewed_asset_id", None) != asset.asset_id:
            return False
        return getattr(self.preview, "_last_path", None) is not None

    def _update_preview_inspector_visibility(self, asset: Asset | None = None) -> None:
        """Refresh assigner when a model preview is active; keep Preview tab as default on model change."""
        asset = asset or self.selected_asset()
        show_tools = self._model_preview_is_active(asset)
        if hasattr(self, "preview_pin_row"):
            self.preview_pin_row.setVisible(bool(show_tools and asset and asset.magic == "BMD0"))
        if not show_tools:
            if hasattr(self, "texture_assigner"):
                self.texture_assigner.set_context(asset_id=None, parts=[], textures=[], assignments={})
            return
        if hasattr(self, "preview_inspector_tabs") and asset is not None:
            if getattr(self, "_last_inspector_asset_id", None) != asset.asset_id:
                self.preview_inspector_tabs.setCurrentIndex(0)
                self._last_inspector_asset_id = asset.asset_id
        self._refresh_texture_assigner(asset)

    def _ensure_estimated_assignments(self, asset_id: str) -> bool:
        """Seed manual assignments from automatic GLB/material matching if none saved."""
        saved = self._texture_assignments.get(asset_id)
        if saved:
            return False
        labels = list(getattr(self.preview, "_last_mesh_labels", []))
        paths = list(getattr(self.preview, "_mesh_texture_paths", []))
        estimated = estimate_assignments_from_paths(labels, paths)
        if not estimated:
            return False
        self._texture_assignments[asset_id] = estimated
        return True

    def _load_model_preview_glb(
        self,
        path: Path,
        *,
        asset_id: str,
        fallback_textures: list[Path] | None = None,
        texture_by_name: dict[str, Path] | None = None,
        material_to_texture: dict[str, str] | None = None,
        texture_bind_order: list[str] | None = None,
    ) -> None:
        self.preview.load_glb(
            path,
            fallback_textures=fallback_textures,
            texture_by_name=texture_by_name,
            material_to_texture=material_to_texture,
            texture_bind_order=texture_bind_order,
            mesh_texture_overrides={},
        )
        self._ensure_estimated_assignments(asset_id)
        overrides = self._mesh_texture_overrides_for_asset(asset_id)
        if overrides:
            self.preview.load_glb(
                path,
                fallback_textures=fallback_textures,
                texture_by_name=texture_by_name,
                material_to_texture=material_to_texture,
                texture_bind_order=texture_bind_order,
                mesh_texture_overrides=overrides,
            )
        self._store_preview_mesh_labels(list(getattr(self.preview, "_last_mesh_labels", [])))
        asset = self.assets_by_id.get(asset_id)
        if hasattr(self, "_update_preview_details") and asset is not None:
            self._update_preview_details(asset)
