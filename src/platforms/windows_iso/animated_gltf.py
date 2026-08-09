"""glTF 2.0 writer for Marine Park Empire skinned models and matrix tracks."""
from __future__ import annotations

import json
from pathlib import Path
import struct
from typing import Sequence

import numpy as np

from .am import (
    Am1Mesh, AnimationSet, MatrixKey, Skeleton, animation_clips_in_export_order,
)
from .gltf.apply import apply_platform_glb_policy
from .materials import gltf_alpha_properties


_C = np.asarray(
    ((1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0),
     (0.0, -1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0)),
    dtype=np.float64,
)
_C_INV = np.linalg.inv(_C)


def _target_matrix(source: np.ndarray) -> np.ndarray:
    return _C @ source @ _C_INV


def _quat_from_matrix(rotation: np.ndarray) -> np.ndarray:
    """Return a normalized xyzw quaternion from a 3x3 rotation."""
    m = rotation
    trace = float(np.trace(m))
    if trace > 0.0:
        s = np.sqrt(trace + 1.0) * 2.0
        q = np.asarray(((m[2, 1] - m[1, 2]) / s, (m[0, 2] - m[2, 0]) / s,
                        (m[1, 0] - m[0, 1]) / s, 0.25 * s))
    else:
        i = int(np.argmax(np.diag(m)))
        if i == 0:
            s = np.sqrt(max(0.0, 1.0 + m[0, 0] - m[1, 1] - m[2, 2])) * 2.0
            q = np.asarray((0.25 * s, (m[0, 1] + m[1, 0]) / s,
                            (m[0, 2] + m[2, 0]) / s, (m[2, 1] - m[1, 2]) / s))
        elif i == 1:
            s = np.sqrt(max(0.0, 1.0 + m[1, 1] - m[0, 0] - m[2, 2])) * 2.0
            q = np.asarray(((m[0, 1] + m[1, 0]) / s, 0.25 * s,
                            (m[1, 2] + m[2, 1]) / s, (m[0, 2] - m[2, 0]) / s))
        else:
            s = np.sqrt(max(0.0, 1.0 + m[2, 2] - m[0, 0] - m[1, 1])) * 2.0
            q = np.asarray(((m[0, 2] + m[2, 0]) / s, (m[1, 2] + m[2, 1]) / s,
                            0.25 * s, (m[1, 0] - m[0, 1]) / s))
    length = np.linalg.norm(q)
    return q / length if length else np.asarray((0.0, 0.0, 0.0, 1.0))


def _decompose(matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    translation = np.asarray(matrix[:3, 3], dtype=np.float64)
    basis = np.asarray(matrix[:3, :3], dtype=np.float64)
    scale = np.linalg.norm(basis, axis=0)
    scale[scale < 1e-12] = 1.0
    rotation = basis / scale
    u, _, vh = np.linalg.svd(rotation)
    rotation = u @ vh
    if np.linalg.det(rotation) < 0:
        rotation[:, 2] *= -1
        scale[2] *= -1
    return translation, _quat_from_matrix(rotation), scale


def _quat_slerp(a: np.ndarray, b: np.ndarray, amount: float) -> np.ndarray:
    dot = float(np.dot(a, b))
    if dot < 0.0:
        b = -b
        dot = -dot
    if dot > 0.9995:
        value = a + amount * (b - a)
        return value / np.linalg.norm(value)
    theta = np.arccos(np.clip(dot, -1.0, 1.0))
    sine = np.sin(theta)
    return (np.sin((1.0 - amount) * theta) / sine) * a + (np.sin(amount * theta) / sine) * b


def _compose(translation: np.ndarray, quaternion: np.ndarray, scale: np.ndarray) -> np.ndarray:
    x, y, z, w = quaternion
    rotation = np.asarray((
        (1 - 2*y*y - 2*z*z, 2*x*y - 2*z*w, 2*x*z + 2*y*w),
        (2*x*y + 2*z*w, 1 - 2*x*x - 2*z*z, 2*y*z - 2*x*w),
        (2*x*z - 2*y*w, 2*y*z + 2*x*w, 1 - 2*x*x - 2*y*y),
    ))
    result = np.eye(4, dtype=np.float64)
    result[:3, :3] = rotation * scale
    result[:3, 3] = translation
    return result


def _evaluate(track: tuple[MatrixKey, ...], time_ms: float) -> np.ndarray:
    if time_ms <= track[0].time_ms:
        return track[0].matrix
    if time_ms >= track[-1].time_ms:
        return track[-1].matrix
    low = 0
    high = len(track) - 1
    while high - low > 1:
        middle = (low + high) // 2
        if track[middle].time_ms <= time_ms:
            low = middle
        else:
            high = middle
    left, right = track[low], track[high]
    amount = (time_ms - left.time_ms) / (right.time_ms - left.time_ms)
    lt, lq, ls = _decompose(left.matrix)
    rt, rq, rs = _decompose(right.matrix)
    return _compose(lt + (rt - lt) * amount, _quat_slerp(lq, rq, amount), ls + (rs - ls) * amount)


class _Glb:
    def __init__(self) -> None:
        self.binary = bytearray()
        self.views: list[dict] = []
        self.accessors: list[dict] = []

    def view(self, payload: bytes, *, target: int | None = None) -> int:
        self.binary.extend(b"\0" * ((-len(self.binary)) % 4))
        offset = len(self.binary)
        self.binary.extend(payload)
        value = {"buffer": 0, "byteOffset": offset, "byteLength": len(payload)}
        if target is not None:
            value["target"] = target
        self.views.append(value)
        return len(self.views) - 1

    def array(self, values: np.ndarray, kind: str, *, target: int | None = None,
              bounds: bool = False) -> int:
        data = np.ascontiguousarray(values)
        components = {np.dtype("float32"): 5126, np.dtype("uint16"): 5123,
                      np.dtype("uint32"): 5125}[data.dtype]
        view = self.view(data.tobytes(), target=target)
        width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[kind]
        accessor = {"bufferView": view, "componentType": components,
                    "count": int(data.size // width), "type": kind}
        if bounds:
            shaped = data.reshape(-1, width)
            accessor["min"] = shaped.min(axis=0).astype(float).tolist()
            accessor["max"] = shaped.max(axis=0).astype(float).tolist()
        self.accessors.append(accessor)
        return len(self.accessors) - 1


def _bind_geometry(mesh: Am1Mesh, bind_globals: Sequence[np.ndarray]) -> tuple[np.ndarray, ...]:
    positions: list[np.ndarray] = []
    normals: list[np.ndarray] = []
    # glTF's interoperable skinning path is four normalized weights. The DAE
    # archive preserves every source weight; the web preview merges duplicate
    # bone records and retains the four strongest contributions.
    joints = np.zeros((len(mesh.influences), 4), dtype=np.uint16)
    weights = np.zeros((len(mesh.influences), 4), dtype=np.float32)
    for vertex_index, influences in enumerate(mesh.influences):
        position = np.zeros(3, dtype=np.float64)
        normal = np.zeros(3, dtype=np.float64)
        merged_weights: dict[int, float] = {}
        for influence in influences:
            matrix = bind_globals[influence.bone]
            position += influence.weight * (matrix @ np.r_[influence.position, 1.0])[:3]
            normal += influence.weight * (matrix[:3, :3] @ np.asarray(influence.normal))
            if influence.weight > 0.0:
                merged_weights[influence.bone] = merged_weights.get(influence.bone, 0.0) + influence.weight
        strongest = sorted(merged_weights.items(), key=lambda item: item[1], reverse=True)[:4]
        total = sum(weight for _, weight in strongest) or 1.0
        for slot, (bone, weight) in enumerate(strongest):
            joints[vertex_index, slot] = bone
            weights[vertex_index, slot] = weight / total
        length = np.linalg.norm(normal)
        positions.append((_C @ np.r_[position, 1.0])[:3])
        if not length:
            normal = np.asarray((0.0, 0.0, 1.0))
            length = 1.0
        normals.append((_C @ np.r_[normal / length, 0.0])[:3])
    return (np.asarray(positions, dtype=np.float32), np.asarray(normals, dtype=np.float32),
            joints, weights)


def write_animated_glb(mesh: Am1Mesh, skeleton: Skeleton,
                       animation: AnimationSet | Sequence[AnimationSet],
                       output: Path, *, texture_pngs: Sequence[Path | None] = (),
                       max_clips: int | None = None) -> Path:
    """Write one self-contained skinned GLB containing every named AM2 action."""
    animation_sets = [animation] if isinstance(animation, AnimationSet) else list(animation)
    if (not animation_sets or mesh.bone_count != len(skeleton.bones) or
            any(len(item.tracks) != len(skeleton.bones) for item in animation_sets)):
        raise ValueError("AM1, AM2, and AM3 bone counts do not match")
    source_bind = [_evaluate(track, 0.0) for track in animation_sets[0].tracks]
    target_bind = [_target_matrix(matrix) for matrix in source_bind]
    positions, normals, joints, weights = _bind_geometry(mesh, source_bind)
    glb = _Glb()
    uvs = np.asarray(mesh.uvs, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.uint32)
    face_slots = np.asarray(mesh.face_materials)
    attributes = {
        "POSITION": glb.array(positions, "VEC3", target=34962, bounds=True),
        "NORMAL": glb.array(normals, "VEC3", target=34962),
        "TEXCOORD_0": glb.array(uvs, "VEC2", target=34962),
        "JOINTS_0": glb.array(joints[:, :4], "VEC4", target=34962),
        "WEIGHTS_0": glb.array(weights[:, :4], "VEC4", target=34962),
    }
    images: list[dict] = []
    textures: list[dict] = []
    materials: list[dict] = []
    for slot in range(max(mesh.face_materials, default=0) + 1):
        # V3D commonly splits opaque bodies and cutout fins/details into
        # multiple mesh records while declaring one shared texture.
        texture_path = (
            texture_pngs[slot] if slot < len(texture_pngs)
            else texture_pngs[0] if texture_pngs else None
        )
        pbr: dict = {"metallicFactor": 0.0, "roughnessFactor": 1.0,
                     "baseColorFactor": [1.0, 1.0, 1.0, 1.0]}
        if texture_path is not None:
            image_index = len(images)
            images.append({"bufferView": glb.view(Path(texture_path).read_bytes()), "mimeType": "image/png"})
            textures.append({"source": image_index})
            pbr["baseColorTexture"] = {"index": len(textures) - 1}
        material = {"name": f"mpe_material_{slot}", "pbrMetallicRoughness": pbr,
                    "doubleSided": True}
        selected_faces = faces[face_slots == slot]
        material.update(gltf_alpha_properties(
            texture_path, uvs=uvs, faces=selected_faces,
        ))
        materials.append(material)
    primitives: list[dict] = []
    for slot in range(len(materials)):
        selected = faces[face_slots == slot].reshape(-1)
        if selected.size:
            primitives.append({"attributes": attributes,
                               "indices": glb.array(selected, "SCALAR", target=34963),
                               "material": slot})

    inverse_bind = np.asarray([np.linalg.inv(matrix).T.reshape(-1) for matrix in target_bind], dtype=np.float32)
    inverse_accessor = glb.array(inverse_bind, "MAT4")
    nodes: list[dict] = []
    for index, bone in enumerate(skeleton.bones):
        parent = skeleton.parents[index]
        local = target_bind[index] if parent is None else np.linalg.inv(target_bind[parent]) @ target_bind[index]
        translation, rotation, scale = _decompose(local)
        node = {"name": bone.name, "translation": translation.astype(float).tolist(),
                "rotation": rotation.astype(float).tolist(), "scale": scale.astype(float).tolist()}
        if bone.children:
            node["children"] = list(bone.children)
        nodes.append(node)
    mesh_node = len(nodes)
    nodes.append({"name": "Marine Park Empire model", "mesh": 0, "skin": 0})

    animations: list[dict] = []
    for animation_set, clip_name, start_ms, end_ms in animation_clips_in_export_order(
        animation_sets,
    ):
        if max_clips is not None and len(animations) >= max_clips:
            break
        samplers: list[dict] = []
        channels: list[dict] = []
        for bone_index, track in enumerate(animation_set.tracks):
            times = sorted({start_ms, end_ms, *(
                key.time_ms for key in track if start_ms < key.time_ms < end_ms
            )})
            local_translations: list[np.ndarray] = []
            local_rotations: list[np.ndarray] = []
            local_scales: list[np.ndarray] = []
            parent = skeleton.parents[bone_index]
            for time_ms in times:
                child_global = _target_matrix(_evaluate(track, time_ms))
                local = child_global
                if parent is not None:
                    parent_global = _target_matrix(_evaluate(animation_set.tracks[parent], time_ms))
                    local = np.linalg.inv(parent_global) @ child_global
                translation, rotation, scale = _decompose(local)
                local_translations.append(translation)
                local_rotations.append(rotation)
                local_scales.append(scale)
            input_accessor = glb.array(
                np.asarray([(time - start_ms) / 1000.0 for time in times], dtype=np.float32),
                "SCALAR", bounds=True,
            )
            for path, values in (("translation", local_translations), ("rotation", local_rotations)):
                output_accessor = glb.array(
                    np.asarray(values, dtype=np.float32),
                    "VEC3" if path == "translation" else "VEC4",
                )
                samplers.append({"input": input_accessor, "output": output_accessor, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": bone_index, "path": path}})
            scale_values = np.asarray(local_scales, dtype=np.float32)
            if not np.allclose(scale_values, 1.0, atol=1e-5):
                output_accessor = glb.array(scale_values, "VEC3")
                samplers.append({"input": input_accessor, "output": output_accessor, "interpolation": "LINEAR"})
                channels.append({"sampler": len(samplers) - 1,
                                 "target": {"node": bone_index, "path": "scale"}})
        animations.append({"name": clip_name, "samplers": samplers, "channels": channels})

    document = {
        "asset": {"version": "2.0", "generator": "RAE Windows ISO"},
        "scene": 0,
        "scenes": [{"nodes": [mesh_node, *[i for i, parent in enumerate(skeleton.parents) if parent is None]]}],
        "nodes": nodes, "meshes": [{"name": "Marine Park Empire model", "primitives": primitives}],
        "skins": [{"name": "Marine Park Empire skeleton", "joints": list(range(len(skeleton.bones))),
                   "inverseBindMatrices": inverse_accessor}],
        "animations": animations, "materials": materials,
        "buffers": [{"byteLength": len(glb.binary)}], "bufferViews": glb.views, "accessors": glb.accessors,
    }
    if images:
        document["images"] = images
        document["textures"] = textures
        document["samplers"] = [{"magFilter": 9729, "minFilter": 9987, "wrapS": 10497, "wrapT": 10497}]
        for texture in textures:
            texture["sampler"] = 0
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    binary = bytes(glb.binary) + b"\0" * ((-len(glb.binary)) % 4)
    body = struct.pack("<I4s", len(encoded), b"JSON") + encoded + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    apply_platform_glb_policy(output)
    return output
