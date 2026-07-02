"""Tests for merging multiple glTF animations into one clip."""
from __future__ import annotations

from rae.glb_policy.glb_io import GlbData
from rae.glb_policy.merge_animations import merge_glb_animations


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
