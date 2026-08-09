"""Tests for merging multiple glTF animations into one clip."""
from __future__ import annotations

import struct

from rae.platforms.nds.gltf.glb_io import GlbData
from rae.platforms.nds.gltf.merge_animations import (
    merge_glb_animations,
    merge_glb_skeletal_animations,
    retain_default_skeletal_animation,
    scale_glb_skeletal_animation_durations,
)


def test_merge_glb_animations_combines_disjoint_channels() -> None:
    glb = GlbData(
        json={
            "asset": {"version": "2.0"},
            "animations": [
                {
                    "name": "grass",
                    "samplers": [{"input": 0, "output": 1}],
                    "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
                },
                {
                    "name": "disk",
                    "samplers": [{"input": 2, "output": 3}],
                    "channels": [{"sampler": 0, "target": {"node": 2, "path": "translation"}}],
                },
            ],
        },
        bin_chunk=b"",
    )

    merged = merge_glb_animations(glb, name="platform")
    animations = merged.json["animations"]
    assert len(animations) == 1
    assert animations[0]["name"] == "platform"
    assert len(animations[0]["samplers"]) == 2
    assert len(animations[0]["channels"]) == 2
    assert animations[0]["channels"][1]["sampler"] == 1


def test_merge_glb_animations_renames_single_clip() -> None:
    glb = GlbData(
        json={
            "asset": {"version": "2.0"},
            "animations": [
                {
                    "name": "old",
                    "samplers": [],
                    "channels": [],
                }
            ],
        },
        bin_chunk=b"",
    )
    merged = merge_glb_animations(glb, name="Animation")
    assert merged.json["animations"][0]["name"] == "Animation"


def test_merge_skeletal_clips_scales_copied_timeline_and_preserves_property_clip() -> None:
    glb = GlbData(
        json={
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 8}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": 2,
                    "type": "SCALAR",
                    "min": [0.0],
                    "max": [1.0],
                }
            ],
            "animations": [
                {
                    "name": "windmill",
                    "samplers": [{"input": 0, "output": 0}],
                    "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
                },
                {
                    "name": "fountain",
                    "samplers": [{"input": 0, "output": 0}],
                    "channels": [{"sampler": 0, "target": {"node": 2, "path": "translation"}}],
                },
                {
                    "name": "area_water",
                    "samplers": [],
                    "channels": [],
                    "extras": {"rae": {"source": "mapMaterialMotion"}},
                },
            ],
        },
        bin_chunk=struct.pack("<2f", 0.0, 1.0),
    )

    merged = merge_glb_skeletal_animations(glb, duration_scale=2.0)

    assert [animation["name"] for animation in merged.json["animations"]] == [
        "area_water",
        "Exact map object animations",
    ]
    animation = merged.json["animations"][1]
    assert animation["extras"]["rae"]["source"] == "mapObjectAnimations"
    assert len(animation["channels"]) == 2
    timeline_index = animation["samplers"][0]["input"]
    accessor = merged.json["accessors"][timeline_index]
    view = merged.json["bufferViews"][accessor["bufferView"]]
    values = struct.unpack_from("<2f", merged.bin_chunk, view["byteOffset"])
    assert values == (0.0, 2.0)
    assert struct.unpack_from("<2f", merged.bin_chunk, 0) == (0.0, 1.0)


def test_standalone_prop_states_are_scaled_but_not_merged() -> None:
    glb = GlbData(
        json={
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": 8}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 8}],
            "accessors": [
                {
                    "bufferView": 0,
                    "componentType": 5126,
                    "count": 2,
                    "type": "SCALAR",
                }
            ],
            "animations": [
                {
                    "name": "day",
                    "samplers": [{"input": 0, "output": 0}],
                    "channels": [{"sampler": 0, "target": {"node": 1, "path": "scale"}}],
                },
                {
                    "name": "night",
                    "samplers": [{"input": 0, "output": 0}],
                    "channels": [{"sampler": 0, "target": {"node": 1, "path": "translation"}}],
                },
            ],
        },
        bin_chunk=struct.pack("<2f", 0.0, 1.0),
    )

    scaled = scale_glb_skeletal_animation_durations(glb, duration_scale=2.0)
    default = retain_default_skeletal_animation(scaled)

    assert [animation["name"] for animation in scaled.json["animations"]] == ["day", "night"]
    assert [animation["name"] for animation in default.json["animations"]] == ["day"]
    assert scaled.json["animations"][0]["samplers"][0]["input"] == scaled.json["animations"][1]["samplers"][0]["input"]
