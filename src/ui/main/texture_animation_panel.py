"""Texture flipbook preview: detect families, animation states, play/pause, session save."""
from __future__ import annotations

from pathlib import Path

from ...core.texture_assignments import texture_key_for_path
from ...platforms.nds.texture_assigner import relevant_assigner_texture_paths
from ...core.texture_sequences import (
    detect_material_sequences,
    list_playback_options,
    material_has_playable_states,
    normalize_material_spec,
    preview_frame_for_spec,
)
from ...scanner import Asset
from ..preview.texture_clips import build_clip_preview_payload


class TextureAnimationPanelMixin:
    def _init_texture_animation_state(self) -> None:
        self._texture_sequences: dict[str, dict] = {}

    def _available_texture_keys_for_asset(self, asset_id: str | None) -> set[str]:
        preview = self.preview
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
        keys: set[str] = set()
        for path in paths:
            keys.add(texture_key_for_path(path))
            keys.add(path.stem.casefold())
        keys.update(str(value).casefold() for value in assignments.values() if value)
        for key in getattr(preview, "_texture_by_name", {}):
            if key:
                keys.add(str(key).casefold())
        return keys

    def _sync_texture_sequences(self, asset_id: str) -> None:
        labels = list(getattr(self, "_preview_mesh_labels", []))
        mesh_paths = list(getattr(self.preview, "_mesh_texture_paths", []))
        assignments = dict(self._texture_assignments.get(asset_id, {}))
        keys = self._available_texture_keys_for_asset(asset_id)
        detected = detect_material_sequences(
            labels,
            mesh_paths,
            assignments=assignments,
            available_texture_keys=keys,
        )
        payload = self._texture_sequences.setdefault(asset_id, {"materials": {}})
        stored: dict[str, dict] = payload.setdefault("materials", {})
        merged: dict[str, dict] = {}

        for label, fresh in detected.items():
            existing = stored.get(label)
            spec = normalize_material_spec(dict(existing)) if isinstance(existing, dict) else normalize_material_spec(fresh)
            spec["frames"] = list(fresh.get("frames") or [])
            spec["sequenceBase"] = fresh.get("sequenceBase") or spec.get("sequenceBase") or label
            spec["frameStyle"] = fresh.get("frameStyle") or spec.get("frameStyle")
            if not isinstance(spec.get("states"), dict):
                spec["states"] = {}
            merged[label] = spec

        payload["materials"] = merged

    def _material_sequence_spec(self, asset_id: str | None) -> dict[str, dict]:
        if not asset_id:
            return {}
        if getattr(self, "_last_previewed_asset_id", None) == asset_id:
            self._sync_texture_sequences(asset_id)
        payload = self._texture_sequences.get(asset_id)
        if not isinstance(payload, dict):
            return {}
        materials = payload.get("materials")
        if not isinstance(materials, dict):
            return {}
        return {name: normalize_material_spec(spec) for name, spec in materials.items() if isinstance(spec, dict)}

    def _refresh_texture_states(self, asset: Asset | None = None) -> None:
        widget = getattr(self, "animation_states", None) or getattr(self, "texture_states", None)
        if widget is None:
            return
        asset = asset or self.selected_asset()
        if not asset or asset.magic != "BMD0":
            widget.set_context(asset_id=None, materials={})
            return
        asset_id = asset.asset_id
        widget.set_context(
            asset_id=asset_id,
            materials=self._material_sequence_spec(asset_id),
        )

    def _on_animation_spec_changed(self, material_name: str, spec: dict) -> None:
        asset_id = getattr(self, "_last_previewed_asset_id", None)
        if not asset_id:
            return
        payload = self._texture_sequences.setdefault(asset_id, {"materials": {}})
        materials = payload.setdefault("materials", {})
        materials[material_name] = normalize_material_spec(dict(spec))
        self._sync_texture_clip_preview(asset_id=asset_id)

    def _on_texture_state_changed(self, material_name: str, state_name: str) -> None:
        asset_id = getattr(self, "_last_previewed_asset_id", None)
        if not asset_id:
            return
        payload = self._texture_sequences.setdefault(asset_id, {"materials": {}})
        materials = payload.setdefault("materials", {})
        spec = materials.get(material_name)
        if not isinstance(spec, dict):
            return
        spec = normalize_material_spec(dict(spec))
        spec["activeState"] = state_name
        materials[material_name] = spec
        self._apply_texture_state(asset_id, material_name)

    def _apply_texture_state(self, asset_id: str, material_name: str) -> None:
        spec = self._material_sequence_spec(asset_id).get(material_name)
        if not spec:
            return
        frame_key = preview_frame_for_spec(spec)
        if not frame_key:
            return
        assignments = self._texture_assignments.setdefault(asset_id, {})
        assignments[material_name] = frame_key
        preview = self.preview
        web_view = getattr(preview, "_web_view", None)
        if web_view is not None and web_view.is_available():
            web_view.pause_flipbook()
        self._reload_preview_with_assignments()
        self._refresh_texture_states()
        if hasattr(self, "_update_preview_details"):
            asset = self.assets_by_id.get(asset_id)
            if asset is not None:
                self._update_preview_details(asset)

    def _sync_texture_clip_preview(self, *, asset_id: str | None = None) -> None:
        preview = self.preview
        asset_id = asset_id or getattr(self, "_last_previewed_asset_id", None)
        glb_path = getattr(preview, "_last_path", None)
        if glb_path is None or not Path(glb_path).is_file():
            if hasattr(preview, "_update_flipbook_controls"):
                preview._update_flipbook_controls([], [])
            return

        materials_spec = self._material_sequence_spec(asset_id)
        playable = {
            name: spec
            for name, spec in materials_spec.items()
            if material_has_playable_states(spec)
        }
        playback_options = list_playback_options(playable)
        clips: list[dict] = []
        if playable and playback_options:
            clips = build_clip_preview_payload(
                Path(glb_path),
                playable,
                texture_by_name=getattr(preview, "_texture_by_name", {}),
                fallback_paths=list(getattr(preview, "_fallback_texture_paths", [])),
                playback=playback_options,
            )
        if hasattr(preview, "_update_flipbook_controls"):
            preview._update_flipbook_controls(clips, playback_options, autoplay=False)
        self._refresh_texture_states()

    def _texture_sequence_summary(self, asset: Asset | None) -> str | None:
        asset = asset or self.selected_asset()
        if asset is None:
            return None
        materials = self._material_sequence_spec(asset.asset_id)
        if not materials:
            return None
        parts = []
        for mat_name, spec in materials.items():
            frames = spec.get("frames") or []
            active = spec.get("activeState") or "(none)"
            parts.append(f"{mat_name}: {len(frames)} frames, state={active}")
        return "Animation states: " + "; ".join(parts)
