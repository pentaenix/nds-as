"""Nintendo DS export profile for portable Pokemon Resort ``.tile`` bundles.

This module only packages data produced by the existing NDS model export path.
It does not participate in preview or rendering.
"""
from __future__ import annotations

import json
import base64
import copy
import io
import math
import re
import shutil
import tempfile
import zipfile
from dataclasses import replace
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
from ..gltf.extract import _accessor_values, recenter_glb_geometry
from ..gltf.glb_io import embedded_image_bytes, read_glb

Progress = Callable[[str], None]
FORMAT = "pokemon_resort.tile"
VERSION = 1
TILE_PREVIEW_PATH = "preview.png"
TILE_PREVIEW_SIZE = 512


class TileExportHost(Protocol):
    def _update_status(self, text: str) -> None: ...

    def _pinned_texture_asset(self) -> Asset | None: ...


def _suggest_render_mode(model_glb: Path) -> str:
    """Carry the selected GLB's strongest alpha requirement into the editor."""
    try:
        materials = read_glb(model_glb).json.get("materials") or []
    except Exception:
        return "cutout"
    alpha_modes = {
        str(material.get("alphaMode") or "OPAQUE").upper()
        for material in materials
        if isinstance(material, dict)
    }
    if "BLEND" in alpha_modes:
        return "blend"
    if "MASK" in alpha_modes:
        return "cutout"
    return "opaque"


def _affine_world_uv_materials(model_glb: Path, candidates: set[str]) -> set[str]:
    """Return motion materials whose mesh UVs are one exact X/Z affine field."""
    try:
        glb = read_glb(model_glb)
    except Exception:
        return set()
    materials = glb.json.get("materials") or []
    samples: dict[str, list[tuple[float, float, float, float]]] = {}
    for mesh in glb.json.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            if int(primitive.get("mode", 4)) != 4:
                continue
            material_index = primitive.get("material")
            if not isinstance(material_index, int) or not (0 <= material_index < len(materials)):
                continue
            name = str(materials[material_index].get("name") or f"mat_{material_index}")
            key = name.casefold()
            if key not in candidates:
                continue
            attributes = primitive.get("attributes") or {}
            position_index = attributes.get("POSITION")
            uv_index = attributes.get("TEXCOORD_0")
            if not isinstance(position_index, int) or not isinstance(uv_index, int):
                continue
            positions = _accessor_values(glb, position_index)
            uvs = _accessor_values(glb, uv_index)
            used_indices = (
                [int(value[0]) for value in _accessor_values(glb, primitive["indices"]) if value]
                if isinstance(primitive.get("indices"), int)
                else list(range(min(len(positions), len(uvs))))
            )
            material_samples = samples.setdefault(key, [])
            for vertex_index in used_indices:
                if not (0 <= vertex_index < len(positions) and 0 <= vertex_index < len(uvs)):
                    continue
                position = positions[vertex_index]
                uv = uvs[vertex_index]
                if len(position) >= 3 and len(uv) >= 2:
                    material_samples.append((position[0], position[2], uv[0], uv[1]))

    result: set[str] = set()
    for key, values in samples.items():
        if len(values) < 3:
            continue
        count = float(len(values))
        mean_x = sum(item[0] for item in values) / count
        mean_z = sum(item[1] for item in values) / count
        mean_u = sum(item[2] for item in values) / count
        mean_v = sum(item[3] for item in values) / count
        xx = zz = xz = xu = zu = xv = zv = 0.0
        for x_value, z_value, u_value, v_value in values:
            x_delta = x_value - mean_x
            z_delta = z_value - mean_z
            xx += x_delta * x_delta
            zz += z_delta * z_delta
            xz += x_delta * z_delta
            xu += x_delta * (u_value - mean_u)
            zu += z_delta * (u_value - mean_u)
            xv += x_delta * (v_value - mean_v)
            zv += z_delta * (v_value - mean_v)
        determinant = xx * zz - xz * xz
        if abs(determinant) < 1e-9:
            continue
        u_x = (xu * zz - zu * xz) / determinant
        u_z = (zu * xx - xu * xz) / determinant
        v_x = (xv * zz - zv * xz) / determinant
        v_z = (zv * xx - xv * xz) / determinant
        u_origin = mean_u - u_x * mean_x - u_z * mean_z
        v_origin = mean_v - v_x * mean_x - v_z * mean_z
        max_residual = max(
            math.hypot(
                u_value - (u_origin + u_x * x_value + u_z * z_value),
                v_value - (v_origin + v_x * x_value + v_z * z_value),
            )
            for x_value, z_value, u_value, v_value in values
        )
        if max_residual <= 1e-4:
            result.add(key)
    return result


def _uv_texture_records(model_glb: Path, animations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Describe textured materials separately from pixel-frame animations.

    Resort derives the exact per-tile UV basis from the embedded GLB. This
    manifest section tells importers which materials are ordinary mesh UVs and
    which are globally continuous Nitro UV-motion surfaces.
    """
    try:
        document = read_glb(model_glb).json
    except Exception:
        return []
    motion_materials = {
        str(animation.get("material") or "").casefold()
        for animation in animations
        if animation.get("type") == "materialMotion" and len(animation.get("offsets") or []) > 1
    }
    world_materials = _affine_world_uv_materials(model_glb, motion_materials)

    def wrap(value: Any) -> str:
        if int(value or _REPEAT) == _CLAMP_TO_EDGE:
            return "clamp"
        if int(value or _REPEAT) == _MIRRORED_REPEAT:
            return "mirror"
        return "repeat"

    def filter_mode(value: Any) -> str:
        return "nearest" if int(value or 9729) in {9728, 9984, 9986} else "linear"

    textures = document.get("textures") or []
    samplers = document.get("samplers") or []
    records: list[dict[str, Any]] = []
    for material_index, material in enumerate(document.get("materials") or []):
        pbr = material.get("pbrMetallicRoughness") or {}
        texture_info = pbr.get("baseColorTexture") or {}
        texture_index = texture_info.get("index")
        if not isinstance(texture_index, int) or not (0 <= texture_index < len(textures)):
            continue
        texture = textures[texture_index] or {}
        sampler_index = texture.get("sampler")
        sampler = samplers[sampler_index] if isinstance(sampler_index, int) and 0 <= sampler_index < len(samplers) else {}
        name = str(material.get("name") or f"mat_{material_index}")
        records.append({
            "material": name,
            "coordinateSpace": "world" if name.casefold() in world_materials else "mesh",
            "sampler": {
                "wrapS": wrap(sampler.get("wrapS")),
                "wrapT": wrap(sampler.get("wrapT")),
                "magFilter": filter_mode(sampler.get("magFilter")),
                "minFilter": filter_mode(sampler.get("minFilter")),
            },
        })
    return records


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


_CLAMP_TO_EDGE = 33071
_MIRRORED_REPEAT = 33648
_REPEAT = 10497


def _wrapped_pixel_index(value: int, size: int, wrap: int) -> int:
    if size <= 1:
        return 0
    if wrap == _CLAMP_TO_EDGE:
        return min(size - 1, max(0, value))
    if wrap == _MIRRORED_REPEAT:
        period = size * 2
        mirrored = value % period
        return mirrored if mirrored < size else period - 1 - mirrored
    return value % size


def _offset_with_sampler(image, shift_x: int, shift_y: int, wrap_s: int, wrap_t: int):
    from PIL import Image, ImageChops

    if wrap_s == _REPEAT and wrap_t == _REPEAT:
        return ImageChops.offset(image, shift_x, shift_y)
    source = image.convert("RGBA")
    pixels = source.load()
    xs = [_wrapped_pixel_index(x - shift_x, source.width, wrap_s) for x in range(source.width)]
    ys = [_wrapped_pixel_index(y - shift_y, source.height, wrap_t) for y in range(source.height)]
    output = Image.new("RGBA", source.size)
    output.putdata([pixels[x, y] for y in ys for x in xs])
    return output


def _motion_frame_image(
    track: dict,
    base_image,
    frame: int,
    *,
    wrap_s: int = _REPEAT,
    wrap_t: int = _REPEAT,
):
    from PIL import Image

    image = base_image
    keyframes = list(track.get("imageKeyframes") or [])
    if keyframes:
        pattern_count = max(1, int(track.get("frameCount") or 1))
        pattern_frame = frame % pattern_count
        selected = keyframes[0]
        for keyframe in keyframes:
            if int(keyframe.get("frame") or 0) <= pattern_frame:
                selected = keyframe
            else:
                break
        payload = str(selected.get("image") or "")
        if payload.startswith("data:") and "," in payload:
            try:
                raw = base64.b64decode(payload.split(",", 1)[1])
                with Image.open(io.BytesIO(raw)) as decoded:
                    image = decoded.convert("RGBA")
            except Exception:
                image = base_image
    offsets = list(track.get("frameOffsets") or [])
    if offsets:
        u, v = offsets[frame % len(offsets)]
        shift_x = -int(round(float(u) * image.width))
        shift_y = int(round(float(v) * image.height))
        if shift_x or shift_y:
            image = _offset_with_sampler(image, shift_x, shift_y, wrap_s, wrap_t)
    return image


def _material_motion_animations(model_glb: Path, staging: Path) -> list[dict[str, Any]]:
    """Preserve Nitro material motion without quantizing UVs into bitmaps.

    RAE previews these tracks by applying the original per-frame UV offsets and
    sparse pattern keyframes. Exporting that same representation keeps a tile
    visually identical to the DS preview and avoids hundreds of duplicate PNGs.
    """
    glb = read_glb(model_glb)
    motion = (((glb.json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
    clips = [clip for clip in (motion.get("clips") or []) if clip.get("tracks")]
    if not clips:
        return []
    default_id = str(motion.get("defaultClip") or clips[0].get("id") or "")
    clip = next((candidate for candidate in clips if str(candidate.get("id") or "") == default_id), clips[0])
    from ..material_animation import normalize_gen5_shoreline_motion

    source_tracks = [
        {
            **track,
            "frameOffsets": [list(value) for value in (track.get("frameOffsets") or [])],
        }
        for track in (clip.get("tracks") or [])
        if isinstance(track, dict)
    ]
    normalize_gen5_shoreline_motion(source_tracks)
    fps = max(1, int(motion.get("frameRate") or 30))
    source_fps = max(1, int(motion.get("sourceFrameRate") or fps))
    layer_semantics = {
        # Preserve the Gen 5 shoreline display-list stack when the tile is
        # separated from its source map and recomposed by Pokemon Resort.
        "sea_zanami2": (20, "shoreline-underlay"),
        "sea_simi_1": (21, "shoreline-wet-sand"),
        "sea_zanami": (22, "shoreline-crest"),
        # The two ocean sheets are authored at different heights, but an
        # explicit order keeps previews and runtimes deterministic as well.
        "sea_mizu1": (10, "water-lower"),
        "sea_mizu1_1": (11, "water-upper"),
    }
    animations: list[dict[str, Any]] = []
    for track in source_tracks:
        material = str(track.get("material") or "")
        if not material:
            continue
        offsets = [
            [float(value[0]), float(value[1])]
            for value in (track.get("frameOffsets") or [])
            if isinstance(value, (list, tuple)) and len(value) >= 2
        ]
        image_keyframes: list[dict[str, Any]] = []
        material_dir = _safe_component(material, "material")
        for keyframe_index, keyframe in enumerate(track.get("imageKeyframes") or []):
            if not isinstance(keyframe, dict):
                continue
            payload = str(keyframe.get("image") or "")
            if not payload.startswith("data:") or "," not in payload:
                continue
            try:
                raw = base64.b64decode(payload.split(",", 1)[1])
            except Exception:
                continue
            relative = Path("textures") / material_dir / f"keyframe_{keyframe_index:03d}.png"
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            image_keyframes.append({
                "frame": max(0, int(keyframe.get("frame") or 0)),
                "path": relative.as_posix(),
            })
        cycles = []
        if offsets:
            cycles.append(len(offsets))
        if image_keyframes:
            cycles.append(max(1, int(track.get("frameCount") or 1)))
        frame_count = math.lcm(*cycles) if cycles else int(clip.get("frameCount") or 0)
        if frame_count < 2 or (len(offsets) < 2 and len(image_keyframes) < 2):
            continue
        track_fps = max(1, int(track.get("frameRate") or fps))
        render_order, layer_role = layer_semantics.get(material.casefold(), (0, "surface"))
        animations.append(
            {
                "material": material,
                "type": "materialMotion",
                "state": str(clip.get("id") or "play"),
                "frameCount": frame_count,
                **({"offsets": offsets} if offsets else {}),
                **({"imageKeyframes": image_keyframes} if image_keyframes else {}),
                **(
                    {"imageFrameCount": max(1, int(track.get("frameCount") or 1))}
                    if image_keyframes
                    else {}
                ),
                "frameDurationMs": max(16, round(1000 / track_fps)),
                # frameDurationMs remains for v1 readers. timebaseHz is the
                # authoritative sample cadence and avoids drift from rounded
                # millisecond durations (for example 15 Hz becoming 14.925 Hz
                # when represented only as 67 ms).
                "timebaseHz": track_fps,
                "sourceTimebaseHz": source_fps,
                "interpolation": "step",
                "renderOrder": render_order,
                "layerRole": layer_role,
                "loop": clip.get("loop") is not False,
                "phase": "global",
            }
        )
    return animations


def _merge_animation_sources(*sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for source in sources:
        for animation in source:
            key = str(animation.get("material") or "").casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(animation)
    return merged


def render_tile_preview_png(
    bundle: ModelPreviewBundle,
    *,
    model_glb: Path | None = None,
    size: int = TILE_PREVIEW_SIZE,
    motion_frame: int | None = 0,
) -> bytes | None:
    """Render a transparent top-down orthographic placement preview.

    The snapshot renderer first converts exported Y-up geometry into its
    preview axes, where looking along the zero-pitch camera axis is straight
    down onto the X/Z placement plane. The result is a true orthographic
    projection rather than a perspective beauty shot.
    """
    from ....model_preview.scene_snapshot import render_model_preview_snapshot

    source_model = Path(model_glb or bundle.patched_glb)
    with tempfile.TemporaryDirectory(prefix="rae_tile_preview_") as temp_name:
        preview_model = source_model
        if motion_frame is not None:
            animated_model = Path(temp_name) / "preview_frame.glb"
            if _write_motion_preview_model(source_model, animated_model, int(motion_frame)):
                preview_model = animated_model
        preview_bundle = replace(
            bundle,
            source_glb=preview_model,
            patched_glb=preview_model,
        )
        png, _, _ = render_model_preview_snapshot(
            preview_bundle,
            width=max(64, int(size)),
            height=max(64, int(size)),
            yaw_deg=0.0,
            pitch_deg=0.0,
            zoom_factor=1.08,
        )
        return png


def _write_motion_preview_model(source: Path, output: Path, frame: int) -> bool:
    """Bake one material-motion frame into a temporary preview-only GLB.

    The CPU thumbnail renderer does not evaluate ``EXT_property_animation``.
    Baking the selected frame keeps exported previews aligned with RAE's live
    viewport without changing the model or animation stored in the tile.
    """
    from PIL import Image

    glb = read_glb(source)
    motion = (((glb.json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
    clips = [clip for clip in (motion.get("clips") or []) if isinstance(clip, dict)]
    if not clips:
        return False
    default_id = str(motion.get("defaultClip") or clips[0].get("id") or "")
    clip = next((item for item in clips if str(item.get("id") or "") == default_id), clips[0])
    tracks = {
        str(track.get("material") or "").casefold(): track
        for track in (clip.get("tracks") or [])
        if isinstance(track, dict) and track.get("material")
    }
    if not tracks:
        return False

    materials = glb.json.get("materials") or []
    textures = glb.json.get("textures") or []
    samplers = glb.json.get("samplers") or []
    changed = False
    for material in materials:
        if not isinstance(material, dict):
            continue
        track = tracks.get(str(material.get("name") or "").casefold())
        if track is None:
            continue
        texture_info = ((material.get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {})
        texture_index = texture_info.get("index")
        if not isinstance(texture_index, int) or not (0 <= texture_index < len(textures)):
            continue
        texture = textures[texture_index]
        image_index = texture.get("source") if isinstance(texture, dict) else None
        if not isinstance(image_index, int):
            continue
        payload = embedded_image_bytes(glb, image_index)
        if not payload:
            continue
        try:
            with Image.open(io.BytesIO(payload)) as decoded:
                image = decoded.convert("RGBA")
        except Exception:
            continue
        sampler_index = texture.get("sampler")
        sampler = (
            samplers[sampler_index]
            if isinstance(sampler_index, int) and 0 <= sampler_index < len(samplers)
            else {}
        )
        baked = _motion_frame_image(
            track,
            image,
            frame,
            wrap_s=int(sampler.get("wrapS", _REPEAT)),
            wrap_t=int(sampler.get("wrapT", _REPEAT)),
        )
        encoded = io.BytesIO()
        baked.save(encoded, format="PNG")
        raw = encoded.getvalue()
        padding = (-len(glb.bin_chunk)) % 4
        glb.bin_chunk += b"\x00" * padding
        byte_offset = len(glb.bin_chunk)
        glb.bin_chunk += raw
        buffer_views = glb.json.setdefault("bufferViews", [])
        buffer_views.append({
            "buffer": 0,
            "byteOffset": byte_offset,
            "byteLength": len(raw),
        })
        images = glb.json.setdefault("images", [])
        images.append({"mimeType": "image/png", "bufferView": len(buffer_views) - 1})
        preview_texture = copy.deepcopy(texture)
        preview_texture["source"] = len(images) - 1
        textures.append(preview_texture)
        texture_info["index"] = len(textures) - 1
        changed = True

    if not changed:
        return False
    buffers = glb.json.setdefault("buffers", [{}])
    if buffers:
        buffers[0]["byteLength"] = len(glb.bin_chunk)
    output.parent.mkdir(parents=True, exist_ok=True)
    glb.write(output)
    return True


def composite_tile_preview_over_footprint(
    foreground_png: bytes,
    backdrop_png: bytes,
) -> bytes:
    """Fill only the projected tile footprint behind a layered preview.

    Shoreline tiles intentionally omit the separately placeable open-water
    body.  The catalog preview may show that body beneath transparent wave
    layers, but pixels outside a 1x3 or 3x3 tile footprint stay transparent.
    """
    from PIL import Image

    with Image.open(io.BytesIO(foreground_png)) as source:
        foreground = source.convert("RGBA")
    with Image.open(io.BytesIO(backdrop_png)) as source:
        backdrop = source.convert("RGBA")
    bounds = foreground.getchannel("A").getbbox()
    result = Image.new("RGBA", foreground.size, (0, 0, 0, 0))
    if bounds:
        x0, y0, x1, y1 = bounds
        fitted = backdrop.resize((x1 - x0, y1 - y0), Image.Resampling.NEAREST)
        result.alpha_composite(fitted, (x0, y0))
    result.alpha_composite(foreground)
    encoded = io.BytesIO()
    result.save(encoded, format="PNG", optimize=True)
    return encoded.getvalue()


def replace_tile_archive_preview(
    tile_path: Path,
    preview_png: bytes,
    *,
    preview_size: int = TILE_PREVIEW_SIZE,
) -> Path:
    """Atomically replace a tile preview while preserving every runtime asset."""
    tile_path = Path(tile_path)
    with zipfile.ZipFile(tile_path, "r") as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]
    by_name = {item.filename: payload for item, payload in entries}
    manifest = json.loads(by_name["manifest.json"].decode("utf-8"))
    manifest["preview"] = {
        "path": TILE_PREVIEW_PATH,
        "format": "png",
        "projection": "orthographic",
        "view": "top-down",
        "width": max(64, int(preview_size)),
        "height": max(64, int(preview_size)),
    }
    by_name["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
    by_name[TILE_PREVIEW_PATH] = preview_png
    if TILE_PREVIEW_PATH not in {item.filename for item, _ in entries}:
        entries.append((zipfile.ZipInfo(TILE_PREVIEW_PATH), preview_png))

    with tempfile.NamedTemporaryFile(
        prefix=f".{tile_path.name}.",
        suffix=".tmp",
        dir=tile_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for item, original_payload in entries:
                archive.writestr(item, by_name.get(item.filename, original_payload))
        temporary_path.replace(tile_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return tile_path


def write_tile_archive(
    output_path: Path,
    *,
    model_glb: Path,
    asset: Asset,
    animations: list[dict[str, Any]],
    staging: Path,
    selected_materials: list[str] | None = None,
    name_override: str | None = None,
    source_details: dict[str, Any] | None = None,
    preview_png: bytes | None = None,
    preview_size: int = TILE_PREVIEW_SIZE,
    footprint: tuple[int, int] | None = None,
    default_tags: list[str] | None = None,
    default_properties: dict[str, Any] | None = None,
) -> Path:
    """Write an already-prepared GLB and frame files as a versioned bundle."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    name = str(name_override or "").strip() or Path(asset.virtual_path).stem or asset.asset_id or "tile"
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
            **({"selectedMaterials": selected_materials} if selected_materials else {}),
            **(source_details or {}),
        },
        "model": {"path": "model.glb", "format": "glb"},
        **(
            {
                "preview": {
                    "path": TILE_PREVIEW_PATH,
                    "format": "png",
                    "projection": "orthographic",
                    "view": "top-down",
                    "width": max(64, int(preview_size)),
                    "height": max(64, int(preview_size)),
                }
            }
            if preview_png
            else {}
        ),
        "materials": {
            "animations": animations,
            "uvTextures": _uv_texture_records(model_glb, animations),
        },
        "defaults": {
            **(
                {"width": max(1, int(footprint[0])), "height": max(1, int(footprint[1]))}
                if footprint is not None
                else {}
            ),
            "tags": list(dict.fromkeys(default_tags or [])),
            "properties": {
                "source.platform": "nds",
                "source.asset": asset.virtual_path,
                **(default_properties or {}),
            },
            "renderMode": _suggest_render_mode(model_glb),
            "collision": {"mode": "none", "autoApply": False},
        },
    }
    manifest_path = staging / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    shutil.copyfile(model_glb, staging / "model.glb")
    if preview_png:
        (staging / TILE_PREVIEW_PATH).write_bytes(preview_png)
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(staging.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(staging).as_posix())
    return output_path


def normalize_tile_archive_shoreline_motion(tile_path: Path) -> int:
    """Migrate one existing NDS ``.tile`` archive to bounded shore motion.

    Older beach bundles can contain the raw Gen 5 tangent phase in both the
    portable manifest and the embedded GLB metadata.  Rewrite both copies so
    RAE, Resort Admin, and the native renderer cannot select conflicting
    shoreline animation data.  All unrelated archive entries are preserved.
    """
    from ..material_animation import (
        normalize_gen5_shoreline_motion,
        sync_material_motion_property_animations,
    )

    tile_path = Path(tile_path)
    with zipfile.ZipFile(tile_path, "r") as archive:
        entries = [(item, archive.read(item.filename)) for item in archive.infolist()]

    by_name = {item.filename: payload for item, payload in entries}
    changed = 0
    manifest_payload = by_name.get("manifest.json")
    if manifest_payload is not None:
        manifest = json.loads(manifest_payload.decode("utf-8"))
        animations = (((manifest.get("materials") or {}).get("animations")) or [])
        manifest_tracks: list[dict[str, Any]] = []
        manifest_animations: list[dict[str, Any]] = []
        for animation in animations:
            if not isinstance(animation, dict) or animation.get("type") != "materialMotion":
                continue
            offsets = animation.get("offsets") or []
            if not offsets:
                continue
            manifest_animations.append(animation)
            manifest_tracks.append({
                "material": animation.get("material"),
                "frameOffsets": [list(value) for value in offsets],
            })
        manifest_changed = normalize_gen5_shoreline_motion(manifest_tracks)
        if manifest_changed:
            for animation, track in zip(manifest_animations, manifest_tracks, strict=True):
                animation["offsets"] = track["frameOffsets"]
            by_name["manifest.json"] = (json.dumps(manifest, indent=2) + "\n").encode("utf-8")
            changed += manifest_changed

    model_payload = by_name.get("model.glb")
    if model_payload is not None:
        with tempfile.TemporaryDirectory(prefix="rae_nds_tile_motion_") as temp_name:
            model_path = Path(temp_name) / "model.glb"
            model_path.write_bytes(model_payload)
            glb = read_glb(model_path)
            motion = (((glb.json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
            glb_changed = sum(
                normalize_gen5_shoreline_motion(clip.get("tracks") or [])
                for clip in (motion.get("clips") or [])
                if isinstance(clip, dict)
            )
            if glb_changed:
                sync_material_motion_property_animations(glb)
                glb.write(model_path)
                by_name["model.glb"] = model_path.read_bytes()
                changed += glb_changed

    if not changed:
        return 0

    with tempfile.NamedTemporaryFile(
        prefix=f".{tile_path.name}.",
        suffix=".tmp",
        dir=tile_path.parent,
        delete=False,
    ) as temporary:
        temporary_path = Path(temporary.name)
    try:
        with zipfile.ZipFile(
            temporary_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for item, original_payload in entries:
                archive.writestr(item, by_name.get(item.filename, original_payload))
        temporary_path.replace(tile_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return changed


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
        model_path = recenter_glb_geometry(model_path, temp / "placement_ready.glb")
        staging = temp / "tile"
        staging.mkdir(parents=True, exist_ok=True)
        animations = _merge_animation_sources(
            _material_animations(_sequence_specs(host, asset, bundle), bundle, staging),
            _material_motion_animations(model_path, staging),
        )
        preview_png = render_tile_preview_png(bundle, model_glb=model_path)
        return write_tile_archive(
            out_dir / filename,
            model_glb=model_path,
            asset=asset,
            animations=animations,
            staging=staging,
            preview_png=preview_png,
        )
