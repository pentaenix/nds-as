"""Merge multiple glTF animations into one clip (DS multi-NSBCA playback)."""
from __future__ import annotations

import copy
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
