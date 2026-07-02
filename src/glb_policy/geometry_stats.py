"""Per-material face-normal statistics from GLB geometry."""
from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

from .glb_io import GlbData

# apicula exports DS models with +Y up; Nitro ground shadows are planes in the XZ plane
# (face normals align with ±Y in model space).
_MODEL_UP = np.array([0.0, 1.0, 0.0], dtype=np.float64)

HORIZONTAL_DOT_THRESHOLD = 0.85
HORIZONTAL_FACE_FRACTION = 0.80


@dataclass(frozen=True)
class MaterialGeometryStats:
    triangle_count: int = 0
    horizontal_face_fraction: float = 0.0


def horizontal_face_fraction_for_normals(normals: np.ndarray) -> float:
    if normals.size == 0:
        return 0.0
    lengths = np.linalg.norm(normals, axis=1)
    valid = lengths > 1e-8
    if not np.any(valid):
        return 0.0
    unit = normals[valid] / lengths[valid, None]
    dots = np.abs(unit @ _MODEL_UP)
    return float(np.mean(dots >= HORIZONTAL_DOT_THRESHOLD))


def _component_type_dtype(component_type: int) -> tuple[str, int]:
    mapping = {
        5120: ("b", 1),
        5121: ("B", 1),
        5122: ("h", 2),
        5123: ("H", 2),
        5125: ("I", 4),
        5126: ("f", 4),
    }
    if component_type not in mapping:
        raise ValueError(f"Unsupported component type {component_type}")
    return mapping[component_type]


def _read_accessor(glb: GlbData, accessor_index: int) -> np.ndarray:
    accessor = glb.json["accessors"][accessor_index]
    count = int(accessor["count"])
    type_name = str(accessor["type"])
    component_type = int(accessor["componentType"])
    fmt_char, component_size = _component_type_dtype(component_type)

    type_components = {
        "SCALAR": 1,
        "VEC2": 2,
        "VEC3": 3,
        "VEC4": 4,
    }
    num_components = type_components[type_name]
    element_size = component_size * num_components

    buffer_view_index = accessor.get("bufferView")
    if buffer_view_index is None:
        return np.zeros((0, num_components), dtype=np.float64)

    buffer_view = glb.json["bufferViews"][int(buffer_view_index)]
    byte_offset = int(accessor.get("byteOffset", 0)) + int(buffer_view.get("byteOffset", 0))
    byte_stride = int(buffer_view.get("byteStride", 0)) or element_size
    byte_length = int(buffer_view.get("byteLength", 0))

    raw = glb.bin_chunk[byte_offset : byte_offset + byte_length]
    if type_name == "VEC3" and fmt_char == "f":
        arr = np.frombuffer(raw, dtype="<f4", count=count * 3).reshape(count, 3)
        return arr.astype(np.float64)

    values = np.zeros((count, num_components), dtype=np.float64)
    for i in range(count):
        start = i * byte_stride
        for c in range(num_components):
            off = start + c * component_size
            values[i, c] = struct.unpack_from("<" + fmt_char, raw, off)[0]
    return values


def _triangle_normals(positions: np.ndarray, indices: np.ndarray) -> np.ndarray:
    normals = []
    for i0, i1, i2 in indices:
        p0 = positions[int(i0)]
        p1 = positions[int(i1)]
        p2 = positions[int(i2)]
        edge1 = p1 - p0
        edge2 = p2 - p0
        n = np.cross(edge1, edge2)
        length = np.linalg.norm(n)
        if length > 1e-8:
            n = n / length
        normals.append(n)
    if not normals:
        return np.zeros((0, 3), dtype=np.float64)
    return np.asarray(normals, dtype=np.float64)


def compute_material_geometry_stats(glb: GlbData) -> dict[int, MaterialGeometryStats]:
    per_material_normals: dict[int, list[np.ndarray]] = {}

    meshes = glb.json.get("meshes") or []
    nodes = glb.json.get("nodes") or []

    def visit_node(node_index: int, parent_matrix: np.ndarray) -> None:
        node = nodes[node_index]
        local = _node_matrix(node)
        world = parent_matrix @ local
        mesh_index = node.get("mesh")
        if mesh_index is not None:
            _accumulate_mesh(glb, int(mesh_index), world, per_material_normals)
        for child in node.get("children") or []:
            visit_node(int(child), world)

    root_nodes = [i for i, node in enumerate(nodes) if not any(
        int(child) == i for n in nodes for child in (n.get("children") or [])
    )]
    identity = np.eye(4, dtype=np.float64)
    for root in root_nodes:
        visit_node(root, identity)

    out: dict[int, MaterialGeometryStats] = {}
    for mat_index, normal_chunks in per_material_normals.items():
        normals = np.vstack(normal_chunks) if normal_chunks else np.zeros((0, 3))
        out[mat_index] = MaterialGeometryStats(
            triangle_count=int(normals.shape[0]),
            horizontal_face_fraction=horizontal_face_fraction_for_normals(normals),
        )
    return out


def _node_matrix(node: dict) -> np.ndarray:
    matrix = np.eye(4, dtype=np.float64)
    if "matrix" in node:
        matrix = np.asarray(node["matrix"], dtype=np.float64).reshape(4, 4).T
        return matrix
    translation = node.get("translation") or [0.0, 0.0, 0.0]
    rotation = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]
    scale = node.get("scale") or [1.0, 1.0, 1.0]
    tx, ty, tz = translation
    qx, qy, qz, qw = rotation
    sx, sy, sz = scale

    xx = qx * qx
    yy = qy * qy
    zz = qz * qz
    xy = qx * qy
    xz = qx * qz
    yz = qy * qz
    wx = qw * qx
    wy = qw * qy
    wz = qw * qz

    rot = np.array(
        [
            [1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy), 0.0],
            [2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx), 0.0],
            [2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy), 0.0],
            [0.0, 0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )
    scale_mat = np.diag([sx, sy, sz, 1.0])
    trans_mat = np.eye(4, dtype=np.float64)
    trans_mat[:3, 3] = [tx, ty, tz]
    return trans_mat @ rot @ scale_mat


def _accumulate_mesh(
    glb: GlbData,
    mesh_index: int,
    world: np.ndarray,
    per_material_normals: dict[int, list[np.ndarray]],
) -> None:
    mesh = glb.json["meshes"][mesh_index]
    normal_matrix = np.linalg.inv(world[:3, :3]).T

    for primitive in mesh.get("primitives") or []:
        attributes = primitive.get("attributes") or {}
        pos_index = attributes.get("POSITION")
        if pos_index is None:
            continue
        positions = _read_accessor(glb, int(pos_index))
        if positions.size == 0:
            continue
        hom = np.concatenate([positions, np.ones((positions.shape[0], 1))], axis=1)
        world_pos = (world @ hom.T).T[:, :3]

        indices_accessor = primitive.get("indices")
        if indices_accessor is not None:
            indices_raw = _read_accessor(glb, int(indices_accessor)).reshape(-1).astype(int)
            indices = indices_raw.reshape(-1, 3)
        else:
            count = positions.shape[0]
            indices = np.arange(count, dtype=int).reshape(-1, 3)

        normals = _triangle_normals(world_pos, indices)
        if normals.size:
            normals = (normal_matrix @ normals.T).T

        mat_index = int(primitive.get("material", -1))
        if mat_index < 0:
            continue
        per_material_normals.setdefault(mat_index, []).append(normals)


def is_predominantly_horizontal(stats: MaterialGeometryStats | None) -> bool:
    if stats is None or stats.triangle_count == 0:
        return False
    return stats.horizontal_face_fraction >= HORIZONTAL_FACE_FRACTION
