"""Nintendo DS export profile for portable Pokemon Resort ``.tile`` bundles.

This module only packages data produced by the existing NDS model export path.
It does not participate in preview or rendering.
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Callable, Protocol

from ....core.assets import Asset
from ....core.texture_assignments import texture_key_for_path
from ....core.texture_sequences import (
    DEFAULT_FRAME_DURATION_MS,
    detect_material_sequences,
    normalize_material_spec,
    playback_frames_for_spec,
    resolve_frame_path,
)
from ....core.util import sanitize_virtual_path
from ....preview_policy import ModelPreviewPolicy
from ..model_module.preview_pipeline import (
    ModelPreviewBundle,
    build_textured_model_bundle,
    finalize_textured_glb_export,
)
from ..texture_library import TextureLibrary

Progress = Callable[[str], None]
FORMAT = "pokemon_resort.tile"
VERSION = 1


class TileExportHost(Protocol):
    def _update_status(self, text: str) -> None: ...

    def _pinned_texture_asset(self) -> Asset | None: ...


def _safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._-")
    return cleaned or fallback


def _available_texture_keys(bundle: ModelPreviewBundle) -> set[str]:
    keys: set[str] = set()
    for key, path in bundle.texture_by_name.items():
        keys.add(str(key).strip().casefold())
        keys.add(Path(path).stem.casefold())
    for path in bundle.fallback_paths:
        keys.add(Path(path).stem.casefold())
        keys.add(texture_key_for_path(Path(path)))
    return {key for key in keys if key}


def _sequence_specs(
    host: TileExportHost,
    asset: Asset,
    bundle: ModelPreviewBundle,
) -> dict[str, dict[str, Any]]:
    getter = getattr(host, "_material_sequence_spec", None)
    stored = getter(asset.asset_id) if callable(getter) else {}
    stored = {
        str(name): normalize_material_spec(spec)
        for name, spec in (stored or {}).items()
        if isinstance(spec, dict)
    }

    assignments_by_asset = getattr(host, "_texture_assignments", {})
    assignments = dict(assignments_by_asset.get(asset.asset_id, {})) if isinstance(assignments_by_asset, dict) else {}
    detected = detect_material_sequences(
        list(bundle.mesh_labels),
        list(bundle.mesh_texture_paths),
        assignments=assignments,
        available_texture_keys=_available_texture_keys(bundle),
    )
    for material_name, fresh in detected.items():
        if material_name not in stored:
            stored[material_name] = normalize_material_spec(fresh)
            continue
        current = stored[material_name]
        if not current.get("frames"):
            current["frames"] = list(fresh.get("frames") or [])
        current["sequenceBase"] = current.get("sequenceBase") or fresh.get("sequenceBase")
    return stored


def _playback(spec: dict[str, Any]) -> tuple[list[str], bool, int, str]:
    normalized = normalize_material_spec(spec)
    active = str(normalized.get("activeState") or "").strip()
    frames, animate, loop, duration_ms = playback_frames_for_spec(
        normalized,
        state_name=active or None,
    )
    if not frames:
        frames = list(normalized.get("frames") or [])
        animate = len(frames) > 1
        loop = bool(normalized.get("loop", True))
        duration_ms = int(normalized.get("frameDurationMs") or DEFAULT_FRAME_DURATION_MS)
    return frames, loop, max(16, int(duration_ms)), active or "play"


def _material_animations(
    specs: dict[str, dict[str, Any]],
    bundle: ModelPreviewBundle,
    staging: Path,
) -> list[dict[str, Any]]:
    animations: list[dict[str, Any]] = []
    for material_name, spec in specs.items():
        frame_keys, loop, duration_ms, state_name = _playback(spec)
        if len(frame_keys) < 2:
            continue
        material_dir = _safe_component(material_name, "material")
        archive_paths: list[str] = []
        for index, frame_key in enumerate(frame_keys):
            source = resolve_frame_path(
                frame_key,
                texture_by_name=bundle.texture_by_name,
                fallback_paths=list(bundle.fallback_paths),
            )
            if source is None:
                archive_paths = []
                break
            suffix = source.suffix.lower() if source.suffix.lower() in {".png", ".jpg", ".jpeg"} else ".png"
            relative = Path("textures") / material_dir / f"frame_{index:03d}{suffix}"
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            archive_paths.append(relative.as_posix())
        if len(archive_paths) < 2:
            continue
        animations.append(
            {
                "material": material_name,
                "type": "frames",
                "state": state_name,
                "frames": archive_paths,
                "frameDurationMs": duration_ms,
                "loop": loop,
                "phase": "global",
            }
        )
    return animations


def write_tile_archive(
    output_path: Path,
    *,
    model_glb: Path,
    asset: Asset,
    animations: list[dict[str, Any]],
    staging: Path,
) -> Path:
    """Write an already-prepared GLB and frame files as a versioned bundle."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    name = Path(asset.virtual_path).stem or asset.asset_id or "tile"
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "name": name,
        "source": {
            "tool": "RAE",
            "platform": "nds",
            "assetId": asset.asset_id,
            "virtualPath": asset.virtual_path,
            "magic": asset.magic,
        },
        "model": {"path": "model.glb", "format": "glb"},
        "materials": {"animations": animations},
        "defaults": {
            "tags": [],
            "properties": {"source.platform": "nds", "source.asset": asset.virtual_path},
            "renderMode": "cutout",
            "collision": {"mode": "none", "autoApply": False},
        },
    }
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(model_glb, staging / "model.glb")
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    return output_path


def export_tile_bundle(
    host: TileExportHost,
    asset: Asset,
    all_assets: list[Asset],
    out_dir: Path,
    *,
    texture_library: TextureLibrary | None,
    policy: ModelPreviewPolicy,
    progress: Progress | None = None,
) -> Path:
    """Convert one NDS model and package it for the Pokemon Resort map editor."""
    stem = sanitize_virtual_path(asset.virtual_path).stem or asset.asset_id or "tile"
    filename = f"{_safe_component(stem, 'tile')}.tile"
    with tempfile.TemporaryDirectory(prefix="rae_nds_tile_") as temp_name:
        temp = Path(temp_name)
        work = temp / "work"
        bundle, error = build_textured_model_bundle(
            asset,
            all_assets,
            work,
            texture_library=texture_library,
            manual_texture=host._pinned_texture_asset(),
            policy=policy,
            cap_for_viewport=False,
            progress=progress,
        )
        if bundle is None:
            raise RuntimeError(f"Tile conversion failed: {error or 'model export did not produce a GLB'}")
        model_dir = temp / "model"
        model_path = finalize_textured_glb_export(bundle, model_dir, glb_filename="model.glb")[0]
        staging = temp / "tile"
        staging.mkdir(parents=True, exist_ok=True)
        animations = _material_animations(_sequence_specs(host, asset, bundle), bundle, staging)
        return write_tile_archive(
            out_dir / filename,
            model_glb=model_path,
            asset=asset,
            animations=animations,
            staging=staging,
        )
