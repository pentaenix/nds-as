"""Keep horizontal battle-stage platforms at bind pose in exported animations."""
from __future__ import annotations

import copy

from .geometry_stats import (
    _read_accessor,
    compute_material_geometry_stats,
    is_predominantly_horizontal,
)
from .glb_io import GlbData

_JOINT_WEIGHT_THRESHOLD = 0.25


def platform_joint_indices(glb: GlbData) -> set[int]:
    """Joint node indices that predominantly skin horizontal (platform) geometry."""
    material_stats = compute_material_geometry_stats(glb)
    horizontal_materials = {
        mat_index
        for mat_index, stats in material_stats.items()
        if is_predominantly_horizontal(stats)
    }
    if not horizontal_materials:
        return set()

    meshes = glb.json.get("meshes") or []
    joints: set[int] = set()

    for node in glb.json.get("nodes") or []:
        mesh_index = node.get("mesh")
        if mesh_index is None:
            continue
        mesh = meshes[int(mesh_index)]
        for primitive in mesh.get("primitives") or []:
            material_index = primitive.get("material")
            if material_index is None or int(material_index) not in horizontal_materials:
                continue
            attributes = primitive.get("attributes") or {}
            joints_accessor = attributes.get("JOINTS_0")
            weights_accessor = attributes.get("WEIGHTS_0")
            if joints_accessor is None or weights_accessor is None:
                continue
            joint_rows = _read_accessor(glb, int(joints_accessor)).reshape(-1, 4)
            weight_rows = _read_accessor(glb, int(weights_accessor)).reshape(-1, 4)
            for joint_row, weight_row in zip(joint_rows, weight_rows):
                for joint_index, weight in zip(joint_row, weight_row):
                    if weight >= _JOINT_WEIGHT_THRESHOLD:
                        joints.add(int(joint_index))

    return joints


def freeze_horizontal_platform_joints(glb: GlbData) -> GlbData:
    """Drop rotation channels on joints bound to horizontal platform geometry.

    DS battle stages often ship one NSBCA per moving part. apicula emits matching
    glTF clips; DCC tools evaluate them at frame 0 on import, which can tilt the
    flat base plate even though RAE's viewport shows the skeletal bind pose.
    """
    platform_joints = platform_joint_indices(glb)
    if not platform_joints:
        return glb

    animations = glb.json.get("animations") or []
    if not animations:
        return glb

    gltf = copy.deepcopy(glb.json)
    changed = False
    for animation in gltf.get("animations") or []:
        if not isinstance(animation, dict):
            continue
        channels = animation.get("channels") or []
        kept = []
        for channel in channels:
            if not isinstance(channel, dict):
                kept.append(channel)
                continue
            target = channel.get("target") or {}
            if (
                target.get("path") == "rotation"
                and target.get("node") in platform_joints
            ):
                changed = True
                continue
            kept.append(channel)
        animation["channels"] = kept

    if not changed:
        return glb
    return GlbData(json=gltf, bin_chunk=glb.bin_chunk)
