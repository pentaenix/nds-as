"""Merge multiple glTF animations into one clip (DS multi-NSBCA playback)."""
from __future__ import annotations

import copy
import struct
from typing import Any

from .glb_io import GlbData


def _channel_target_key(channel: dict[str, Any]) -> tuple[Any, ...]:
    target = channel.get("target") or {}
    if "node" in target:
        return ("node", target.get("node"), target.get("path"))
    return ("property", str(target.get("path") or ""))


def _dedupe_node_channels(channels: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep one channel per animated node path (later clips win on overlap)."""
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        seen[_channel_target_key(channel)] = channel
    return list(seen.values())


def _is_property_animation(animation: dict[str, Any]) -> bool:
    extensions = animation.get("extensions") or {}
    return bool(extensions.get("EXT_property_animation"))


def _is_skeletal_animation(animation: dict[str, Any]) -> bool:
    return any(
        isinstance(((channel.get("target") or {}).get("node")), int)
        for channel in (animation.get("channels") or [])
        if isinstance(channel, dict)
    )


def _append_scaled_timeline_accessor(
    gltf: dict[str, Any],
    binary: bytearray,
    accessor_index: int,
    scale: float,
) -> int:
    """Copy one float/SCALAR timeline and multiply its seconds by ``scale``."""
    accessors = gltf.get("accessors") or []
    views = gltf.get("bufferViews") or []
    if not (0 <= accessor_index < len(accessors)):
        return accessor_index
    accessor = accessors[accessor_index]
    view_index = accessor.get("bufferView") if isinstance(accessor, dict) else None
    if (
        not isinstance(view_index, int)
        or not (0 <= view_index < len(views))
        or accessor.get("componentType") != 5126
        or accessor.get("type") != "SCALAR"
        or accessor.get("sparse")
    ):
        return accessor_index
    view = views[view_index]
    if not isinstance(view, dict) or int(view.get("buffer") or 0) != 0:
        return accessor_index
    count = max(0, int(accessor.get("count") or 0))
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    stride = int(view.get("byteStride") or 4)
    values: list[float] = []
    for index in range(count):
        offset = start + index * stride
        if offset + 4 > len(binary):
            return accessor_index
        values.append(struct.unpack_from("<f", binary, offset)[0] * scale)

    while len(binary) % 4:
        binary.append(0)
    output_start = len(binary)
    for value in values:
        binary.extend(struct.pack("<f", value))
    new_view = {
        "buffer": 0,
        "byteOffset": output_start,
        "byteLength": len(values) * 4,
    }
    new_view_index = len(views)
    views.append(new_view)
    copied = copy.deepcopy(accessor)
    copied["bufferView"] = new_view_index
    copied.pop("byteOffset", None)
    if values:
        copied["min"] = [min(values)]
        copied["max"] = [max(values)]
    new_accessor_index = len(accessors)
    accessors.append(copied)
    return new_accessor_index


def merge_glb_animations(glb: GlbData, *, name: str = "Animation") -> GlbData:
    """Combine skeletal (+ optional material) clips into one timeline.

    Nintendo DS titles often store one NSBCA per moving part (grass ring, disk,
    etc.) but drive them from the same frame counter in-game. apicula emits one
    glTF animation per NSBCA; this merges them for DCC tools that expect a
    single action.
    """
    animations = [item for item in (glb.json.get("animations") or []) if isinstance(item, dict)]
    if not animations:
        return glb

    if len(animations) == 1:
        gltf = copy.deepcopy(glb.json)
        only = dict(animations[0])
        only["name"] = name
        gltf["animations"] = [only]
        return GlbData(json=gltf, bin_chunk=glb.bin_chunk)

    merged_samplers: list[dict[str, Any]] = []
    merged_node_channels: list[dict[str, Any]] = []
    merged_property_channels: list[dict[str, Any]] = []
    uses_property_extension = False

    for animation in animations:
        samplers = [item for item in (animation.get("samplers") or []) if isinstance(item, dict)]
        sampler_offset = len(merged_samplers)
        merged_samplers.extend(samplers)

        property_ext = (animation.get("extensions") or {}).get("EXT_property_animation") or {}
        property_channels = property_ext.get("channels") or []
        if property_channels:
            uses_property_extension = True
            for channel in property_channels:
                if not isinstance(channel, dict):
                    continue
                copied = copy.deepcopy(channel)
                sampler_index = copied.get("sampler")
                if isinstance(sampler_index, int):
                    copied["sampler"] = sampler_index + sampler_offset
                merged_property_channels.append(copied)
            continue

        for channel in animation.get("channels") or []:
            if not isinstance(channel, dict):
                continue
            copied = copy.deepcopy(channel)
            sampler_index = copied.get("sampler")
            if isinstance(sampler_index, int):
                copied["sampler"] = sampler_index + sampler_offset
            merged_node_channels.append(copied)

    merged_node_channels = _dedupe_node_channels(merged_node_channels)

    merged_animation: dict[str, Any] = {
        "name": name,
        "samplers": merged_samplers,
        "channels": merged_node_channels,
    }

    if uses_property_extension:
        if not merged_node_channels:
            merged_node_channels = [{"target": {"path": "scale"}, "sampler": 0}]
            merged_animation["channels"] = merged_node_channels
        merged_animation["extensions"] = {
            "EXT_property_animation": {"channels": merged_property_channels},
        }

    gltf = copy.deepcopy(glb.json)
    gltf["animations"] = [merged_animation]
    if uses_property_extension:
        extensions_used = list(gltf.get("extensionsUsed") or [])
        if "EXT_property_animation" not in extensions_used:
            extensions_used.append("EXT_property_animation")
        gltf["extensionsUsed"] = extensions_used

    return GlbData(json=gltf, bin_chunk=glb.bin_chunk)


def merge_glb_skeletal_animations(
    glb: GlbData,
    *,
    name: str = "Exact map object animations",
    duration_scale: float = 1.0,
) -> GlbData:
    """Merge concurrent model clips while preserving independent material motion.

    Gen 5 map objects are converted as separate GLBs and can each contribute a
    skeletal clip.  The map viewer can run one skeletal action at a time, so the
    placed-object channels must share one action.  Timeline accessors are copied
    before scaling, which keeps unrelated property-animation timing untouched.
    """
    animations = [item for item in (glb.json.get("animations") or []) if isinstance(item, dict)]
    skeletal = [animation for animation in animations if _is_skeletal_animation(animation)]
    if not skeletal:
        return glb
    property_animations = [animation for animation in animations if not _is_skeletal_animation(animation)]
    working_json = copy.deepcopy(glb.json)
    working_json["animations"] = copy.deepcopy(skeletal)
    merged = merge_glb_animations(
        GlbData(json=working_json, bin_chunk=glb.bin_chunk),
        name=name,
    )
    combined = merged.json["animations"][0]
    combined.setdefault("extras", {}).setdefault("rae", {})["source"] = "mapObjectAnimations"

    binary = bytearray(merged.bin_chunk)
    scale = max(0.000001, float(duration_scale))
    if scale != 1.0:
        remapped: dict[int, int] = {}
        for sampler in combined.get("samplers") or []:
            if not isinstance(sampler, dict) or not isinstance(sampler.get("input"), int):
                continue
            old_index = sampler["input"]
            new_index = remapped.get(old_index)
            if new_index is None:
                new_index = _append_scaled_timeline_accessor(
                    merged.json,
                    binary,
                    old_index,
                    scale,
                )
                remapped[old_index] = new_index
            sampler["input"] = new_index

    merged.json["animations"] = [*copy.deepcopy(property_animations), combined]
    buffers = merged.json.setdefault("buffers", [{"byteLength": 0}])
    if not buffers:
        buffers.append({"byteLength": len(binary)})
    else:
        buffers[0]["byteLength"] = len(binary)
    return GlbData(json=merged.json, bin_chunk=bytes(binary))


def scale_glb_skeletal_animation_durations(
    glb: GlbData,
    *,
    duration_scale: float,
) -> GlbData:
    """Slow every skeletal state independently without merging those states."""
    scale = max(0.000001, float(duration_scale))
    if scale == 1.0:
        return glb
    gltf = copy.deepcopy(glb.json)
    binary = bytearray(glb.bin_chunk)
    remapped: dict[int, int] = {}
    for animation in gltf.get("animations") or []:
        if not isinstance(animation, dict) or not _is_skeletal_animation(animation):
            continue
        for sampler in animation.get("samplers") or []:
            if not isinstance(sampler, dict) or not isinstance(sampler.get("input"), int):
                continue
            old_index = sampler["input"]
            new_index = remapped.get(old_index)
            if new_index is None:
                new_index = _append_scaled_timeline_accessor(
                    gltf,
                    binary,
                    old_index,
                    scale,
                )
                remapped[old_index] = new_index
            sampler["input"] = new_index
    buffers = gltf.setdefault("buffers", [{"byteLength": 0}])
    if not buffers:
        buffers.append({"byteLength": len(binary)})
    else:
        buffers[0]["byteLength"] = len(binary)
    return GlbData(json=gltf, bin_chunk=bytes(binary))


def retain_default_skeletal_animation(glb: GlbData) -> GlbData:
    """Keep one prop state for a composed map while retaining material clips."""
    gltf = copy.deepcopy(glb.json)
    animations = [item for item in (gltf.get("animations") or []) if isinstance(item, dict)]
    skeletal = [animation for animation in animations if _is_skeletal_animation(animation)]
    if len(skeletal) <= 1:
        return glb
    default = skeletal[0]
    non_skeletal = [animation for animation in animations if not _is_skeletal_animation(animation)]
    gltf["animations"] = [*non_skeletal, default]
    return GlbData(json=gltf, bin_chunk=glb.bin_chunk)


def strip_all_animations(glb: GlbData) -> GlbData:
    """Return a completely static bind-pose GLB for interaction-only props."""
    gltf = copy.deepcopy(glb.json)
    gltf.pop("animations", None)
    rae = ((gltf.get("extras") or {}).get("rae") or {})
    if isinstance(rae, dict):
        rae.pop("mapMaterialMotion", None)
    return GlbData(json=gltf, bin_chunk=glb.bin_chunk)
