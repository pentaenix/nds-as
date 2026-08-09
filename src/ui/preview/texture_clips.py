"""Build WebEngine texture flipbook payloads for GLB preview."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...core.texture_sequences import normalize_material_spec, playback_frames_for_spec, resolve_frame_path
from ...platforms.nds.gltf.texture_patch import ensure_baked_preview_texture
from .preview_server import get_preview_server


def build_clip_preview_payload(
    glb_path: Path,
    materials_spec: dict[str, dict[str, Any]],
    *,
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
    playback: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Build flipbook clip descriptors with prebaked HTTP texture URLs."""
    server = get_preview_server()
    clips: list[dict[str, Any]] = []
    targets = playback or []
    if not targets:
        for material_name, raw_spec in materials_spec.items():
            spec = normalize_material_spec(raw_spec)
            active = str(spec.get("activeState") or "play")
            targets.append({"material": material_name, "state": active})

    for target in targets:
        material_name = str(target.get("material") or "").strip()
        state_name = str(target.get("state") or "").strip()
        raw_spec = materials_spec.get(material_name)
        if not isinstance(raw_spec, dict):
            continue
        spec = normalize_material_spec(raw_spec)
        frame_keys, animate, loop, duration_ms = playback_frames_for_spec(spec, state_name=state_name or None)
        if not frame_keys:
            continue
        frame_urls: list[str] = []
        for frame_key in frame_keys:
            path = resolve_frame_path(str(frame_key), texture_by_name=texture_by_name, fallback_paths=fallback_paths)
            if path is None:
                frame_urls = []
                break
            baked = ensure_baked_preview_texture(glb_path, material_name, path)
            frame_urls.append(server.model_url(baked))
        if not frame_urls:
            continue
        animated = bool(animate and len(frame_urls) >= 2)
        clip: dict[str, Any] = {
            "material": material_name,
            "state": state_name,
            "frameUrls": frame_urls if animated else frame_urls[:1],
            "animate": animated,
        }
        if animated:
            clip["frameDurationMs"] = duration_ms
            clip["loop"] = loop
        clips.append(clip)
    return clips


def clips_to_json(clips: list[dict[str, Any]]) -> str:
    return json.dumps(clips, separators=(",", ":"))
