"""GLB export for Switch Trinity models."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from .model import Bone, SwitchModel


def _quat_from_euler_xyz(x: float, y: float, z: float) -> tuple[float, float, float, float]:
    cx, sx = math.cos(x * 0.5), math.sin(x * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    cz, sz = math.cos(z * 0.5), math.sin(z * 0.5)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def write_model_glb(model: SwitchModel, out_path: str | Path) -> Path:
    out_path = Path(out_path)
    bin_blob = bytearray()
    accessors: list[dict] = []
    buffer_views: list[dict] = []
    meshes_json: list[dict] = []
    materials_json: list[dict] = []
    nodes_json: list[dict] = [{"name": model.name, "children": []}]
    root_children: list[int] = []

    mat_index: dict[str, int] = {}

    def align4() -> None:
        while len(bin_blob) % 4:
            bin_blob.append(0)

    def append_view(data: bytes, target: int = 34962) -> int:
        align4()
        offset = len(bin_blob)
        bin_blob.extend(data)
        buffer_views.append({"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target})
        return len(buffer_views) - 1

    def append_accessor(
        view: int,
        *,
        comp_type: int,
        count: int,
        type_name: str,
        byte_offset: int = 0,
        max_vals=None,
        min_vals=None,
    ) -> int:
        acc = {
            "bufferView": view,
            "byteOffset": byte_offset,
            "componentType": comp_type,
            "count": count,
            "type": type_name,
        }
        if max_vals is not None:
            acc["max"] = max_vals
        if min_vals is not None:
            acc["min"] = min_vals
        accessors.append(acc)
        return len(accessors) - 1

    bone_count = len(model.bones)

    for sub in model.submeshes:
        if not sub.positions or not sub.indices:
            continue
        vcount = len(sub.positions)
        max_idx = max(sub.indices) if sub.indices else 0
        indices = (
            [min(i, vcount - 1) for i in sub.indices]
            if max_idx >= vcount
            else sub.indices
        )

        pos_data = b"".join(struct.pack("<fff", *p) for p in sub.positions)
        nrm_data = b"".join(struct.pack("<fff", *n) for n in sub.normals)
        uv_data = b"".join(struct.pack("<ff", *t) for t in sub.uvs)
        if max(indices) < 65536:
            idx_data = b"".join(struct.pack("<H", i) for i in indices)
            idx_comp = 5123
        else:
            idx_data = b"".join(struct.pack("<I", i) for i in indices)
            idx_comp = 5125

        pos_view = append_view(pos_data)
        nrm_view = append_view(nrm_data)
        uv_view = append_view(uv_data)
        idx_view = append_view(idx_data, target=34963)

        xs = [p[0] for p in sub.positions]
        ys = [p[1] for p in sub.positions]
        zs = [p[2] for p in sub.positions]
        pos_acc = append_accessor(
            pos_view,
            comp_type=5126,
            count=len(sub.positions),
            type_name="VEC3",
            min_vals=[min(xs), min(ys), min(zs)],
            max_vals=[max(xs), max(ys), max(zs)],
        )
        nrm_acc = append_accessor(nrm_view, comp_type=5126, count=len(sub.normals), type_name="VEC3")
        uv_acc = append_accessor(uv_view, comp_type=5126, count=len(sub.uvs), type_name="VEC2")
        idx_acc = append_accessor(idx_view, comp_type=idx_comp, count=len(indices), type_name="SCALAR")

        attrs = {
            "POSITION": pos_acc,
            "NORMAL": nrm_acc,
            "TEXCOORD_0": uv_acc,
        }
        has_skin = (
            bone_count > 0
            and len(sub.joints) == vcount
            and len(sub.weights) == vcount
            and all(max(j) < bone_count for j in sub.joints)
        )
        if has_skin:
            joint_rows: list[tuple[int, int, int, int]] = []
            weight_rows: list[tuple[float, float, float, float]] = []
            for (j0, j1, j2, j3), (w0, w1, w2, w3) in zip(sub.joints, sub.weights):
                w = [max(0.0, w0), max(0.0, w1), max(0.0, w2), max(0.0, w3)]
                total = w[0] + w[1] + w[2] + w[3]
                if total <= 1e-6:
                    w = [1.0, 0.0, 0.0, 0.0]
                    mapped = (0, 0, 0, 0)
                else:
                    w = [v / total for v in w]
                    mapped = (
                        min(j0, bone_count - 1),
                        min(j1, bone_count - 1),
                        min(j2, bone_count - 1),
                        min(j3, bone_count - 1),
                    )
                joint_rows.append(mapped)
                weight_rows.append(tuple(w))
            j_data = b"".join(struct.pack("<4B", *j) for j in joint_rows)
            w_data = b"".join(struct.pack("<ffff", *w) for w in weight_rows)
            j_view = append_view(j_data)
            w_view = append_view(w_data)
            attrs["JOINTS_0"] = append_accessor(j_view, comp_type=5121, count=vcount, type_name="VEC4")
            attrs["WEIGHTS_0"] = append_accessor(w_view, comp_type=5126, count=vcount, type_name="VEC4")

        mat_name = sub.material or "default"
        if mat_name not in mat_index:
            mat_index[mat_name] = len(materials_json)
            materials_json.append(
                {
                    "name": mat_name,
                    "pbrMetallicRoughness": {
                        "baseColorFactor": [0.8, 0.8, 0.8, 1.0],
                        "metallicFactor": 0.0,
                        "roughnessFactor": 0.9,
                    },
                    "doubleSided": True,
                }
            )

        mesh_idx = len(meshes_json)
        meshes_json.append({"primitives": [{"attributes": attrs, "indices": idx_acc, "material": mat_index[mat_name]}]})
        node_idx = len(nodes_json)
        node = {"name": sub.name, "mesh": mesh_idx}
        if has_skin:
            node["skin"] = 0
        nodes_json.append(node)
        root_children.append(node_idx)

    skin_json = None
    if model.bones:
        bone_nodes: list[int] = []
        for i, bone in enumerate(model.bones):
            qx, qy, qz, qw = _quat_from_euler_xyz(*bone.rotation)
            bone_nodes.append(len(nodes_json))
            children: list[int] = []
            nodes_json.append(
                {
                    "name": bone.name,
                    "translation": list(bone.translation),
                    "rotation": [qx, qy, qz, qw],
                    "scale": list(bone.scale),
                    "children": children,
                }
            )
        for i, bone in enumerate(model.bones):
            if 0 <= bone.parent < len(model.bones):
                parent_node = bone_nodes[bone.parent]
                nodes_json[parent_node]["children"].append(bone_nodes[i])
        ibm = []
        for _ in model.bones:
            ibm.extend([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1])
        ibm_view = append_view(b"".join(struct.pack("<f", v) for v in ibm))
        ibm_acc = append_accessor(ibm_view, comp_type=5126, count=len(model.bones), type_name="MAT4")
        skin_json = {"inverseBindMatrices": ibm_acc, "joints": bone_nodes, "skeleton": bone_nodes[0] if bone_nodes else 0}

    nodes_json[0]["children"] = root_children

    gltf = {
        "asset": {"version": "2.0", "generator": "RAE Switch"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes_json,
        "meshes": meshes_json,
        "materials": materials_json or [{"name": "default", "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1.0]}}],
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_blob)}],
    }
    if skin_json:
        gltf["skins"] = [skin_json]

    json_bytes = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(json_bytes) % 4:
        json_bytes += b" "
    json_len = len(json_bytes)
    bin_len = len(bin_blob)
    total = 12 + 8 + json_len + 8 + bin_len
    header = struct.pack("<4sII", b"glTF", 2, total)
    json_chunk = struct.pack("<I4s", json_len, b"JSON") + json_bytes
    bin_chunk = struct.pack("<I4s", bin_len, b"BIN\x00") + bin_blob
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(header + json_chunk + bin_chunk)
    return out_path
