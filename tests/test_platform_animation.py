"""Tests for horizontal platform joint animation freeze."""
from __future__ import annotations

import struct

import numpy as np

from rae.glb_policy.glb_io import GlbData
from rae.glb_policy.platform_animation import freeze_horizontal_platform_joints


def _pack_f32(values: list[float]) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def _glb_with_horizontal_platform_rotation() -> GlbData:
    # Two-triangle horizontal plate in the XZ plane (+Y normal) on joint 1.
    positions = _pack_f32(
        [
            -1.0, 0.0, -1.0,
            1.0, 0.0, -1.0,
            0.0, 0.0, 1.0,
            -1.0, 0.0, -1.0,
            0.0, 0.0, 1.0,
            1.0, 0.0, 1.0,
        ]
    )
    normals = _pack_f32(
        [
            0.0, 1.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 1.0, 0.0,
            0.0, 1.0, 0.0,
        ]
    )
    joints = np.array([[1, 0, 0, 0]] * 6, dtype=np.uint16).tobytes()
    weights = _pack_f32([1.0, 0.0, 0.0, 0.0] * 6)
    indices = struct.pack("<6H", 0, 1, 2, 3, 4, 5)

    bin_chunk = positions + normals + joints + weights + indices
    pos_view = {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}
    norm_view = {"buffer": 0, "byteOffset": len(positions), "byteLength": len(normals)}
    joint_view = {
        "buffer": 0,
        "byteOffset": len(positions) + len(normals),
        "byteLength": len(joints),
    }
    weight_view = {
        "buffer": 0,
        "byteOffset": len(positions) + len(normals) + len(joints),
        "byteLength": len(weights),
    }
    index_view = {
        "buffer": 0,
        "byteOffset": len(positions) + len(normals) + len(joints) + len(weights),
        "byteLength": len(indices),
    }

    gltf = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(bin_chunk)}],
        "bufferViews": [pos_view, norm_view, joint_view, weight_view, index_view],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": 6,
                "type": "VEC3",
                "min": [-1.0, 0.0, -1.0],
                "max": [1.0, 0.0, 1.0],
            },
            {
                "bufferView": 1,
                "componentType": 5126,
                "count": 6,
                "type": "VEC3",
            },
            {
                "bufferView": 2,
                "componentType": 5123,
                "count": 6,
                "type": "VEC4",
            },
            {
                "bufferView": 3,
                "componentType": 5126,
                "count": 6,
                "type": "VEC4",
            },
            {
                "bufferView": 4,
                "componentType": 5123,
                "count": 6,
                "type": "SCALAR",
            },
        ],
        "materials": [{"name": "platform"}],
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {
                            "POSITION": 0,
                            "NORMAL": 1,
                            "JOINTS_0": 2,
                            "WEIGHTS_0": 3,
                        },
                        "indices": 4,
                        "material": 0,
                    }
                ]
            }
        ],
        "nodes": [
            {"name": "root"},
            {"name": "platform", "translation": [0.0, 0.0, 0.0]},
            {"name": "grass", "translation": [0.0, 1.0, 0.0]},
            {
                "name": "skinned_mesh",
                "mesh": 0,
                "skin": 0,
                "children": [0],
            },
        ],
        "skins": [{"joints": [0, 1, 2], "skeleton": 0}],
        "animations": [
            {
                "name": "stage",
                "samplers": [{"input": 0, "output": 1}],
                "channels": [
                    {"sampler": 0, "target": {"node": 1, "path": "rotation"}},
                    {"sampler": 0, "target": {"node": 2, "path": "rotation"}},
                ],
            }
        ],
    }
    return GlbData(json=gltf, bin_chunk=bin_chunk)


def test_freeze_horizontal_platform_joints_strips_platform_rotation_only() -> None:
    glb = _glb_with_horizontal_platform_rotation()
    frozen = freeze_horizontal_platform_joints(glb)
    channels = frozen.json["animations"][0]["channels"]
    targets = {(ch["target"]["node"], ch["target"]["path"]) for ch in channels}
    assert (1, "rotation") not in targets
    assert (2, "rotation") in targets


def test_freeze_horizontal_platform_joints_noop_without_animations() -> None:
    glb = _glb_with_horizontal_platform_rotation()
    glb.json["animations"] = []
    frozen = freeze_horizontal_platform_joints(glb)
    assert frozen is glb
