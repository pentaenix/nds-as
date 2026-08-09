"""Trinity model parsing: TRMDL / TRMSH / TRMBF / TRSKL."""
from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field
from pathlib import PurePosixPath

from . import flatbuf as fb
from .trinity import TrinityArchive

# Vertex usage (TRVertexUsage)
USAGE_POSITION = 1
USAGE_NORMAL = 2
USAGE_TANGENT = 3
USAGE_BINORMAL = 4
USAGE_COLOR = 5
USAGE_TEX_COORD = 6
USAGE_BLEND_INDEX = 7
USAGE_BLEND_WEIGHTS = 8

# Vertex format (subset)
FMT_RGBA8_UNORM = 20
FMT_W8X8Y8Z8 = 22
FMT_W16NORM = 39
FMT_W16FLOAT = 43
FMT_XY32F = 48
FMT_XYZ32F = 51
FMT_W32UINT = 52
FMT_W32FLOAT = 54

INDEX_BYTE = 0
INDEX_SHORT = 1
INDEX_INT = 2


@dataclass(slots=True)
class Submesh:
    name: str
    material: str
    positions: list[tuple[float, float, float]]
    normals: list[tuple[float, float, float]]
    uvs: list[tuple[float, float]]
    indices: list[int]
    joints: list[tuple[int, int, int, int]] = field(default_factory=list)
    weights: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass(slots=True)
class Bone:
    name: str
    parent: int
    translation: tuple[float, float, float]
    rotation: tuple[float, float, float]
    scale: tuple[float, float, float]


@dataclass(slots=True)
class SwitchModel:
    name: str
    submeshes: list[Submesh]
    bones: list[Bone] = field(default_factory=list)


def _read_f16(data: bytes, off: int) -> float:
    u = struct.unpack_from("<H", data, off)[0]
    sign = (u >> 15) & 1
    exp = (u >> 10) & 0x1F
    frac = u & 0x3FF
    if exp == 0:
        val = frac / 1024.0
    elif exp == 31:
        val = float("inf") if frac == 0 else float("nan")
    else:
        val = (1 + frac / 1024.0) * (2 ** (exp - 15))
    return -val if sign else val


def _read_vec3(data: bytes, off: int, fmt: int) -> tuple[float, float, float]:
    if fmt == FMT_XYZ32F:
        return struct.unpack_from("<fff", data, off)
    if fmt == FMT_W16FLOAT:
        return (_read_f16(data, off), _read_f16(data, off + 2), _read_f16(data, off + 4))
    if fmt == FMT_W16NORM:
        sx, sy, sz = struct.unpack_from("<hhh", data, off)
        return (max(-1.0, min(1.0, sx / 32767.0)), max(-1.0, min(1.0, sy / 32767.0)), max(-1.0, min(1.0, sz / 32767.0)))
    if fmt == FMT_W8X8Y8Z8:
        bx, by, bz = data[off : off + 3]
        return (bx / 255.0, by / 255.0, bz / 255.0)
    return (0.0, 0.0, 0.0)


def _read_vec2(data: bytes, off: int, fmt: int) -> tuple[float, float]:
    if fmt == FMT_XY32F:
        return struct.unpack_from("<ff", data, off)
    if fmt == FMT_W16FLOAT:
        return (_read_f16(data, off), _read_f16(data, off + 2))
    if fmt == FMT_W16NORM:
        sx, sy = struct.unpack_from("<hh", data, off)
        return (sx / 32767.0, sy / 32767.0)
    if fmt == FMT_W8X8Y8Z8:
        return (data[off] / 255.0, data[off + 1] / 255.0)
    return (0.0, 0.0)


def _read_blend_indices(data: bytes, off: int, fmt: int) -> tuple[int, int, int, int]:
    if fmt == FMT_W32UINT:
        a, b, c, d = struct.unpack_from("<IIII", data, off)
        return (int(a), int(b), int(c), int(d))
    if fmt == FMT_W8X8Y8Z8:
        return tuple(data[off : off + 4])  # type: ignore[return-value]
    return (0, 0, 0, 0)


def _read_blend_weights(data: bytes, off: int, fmt: int) -> tuple[float, float, float, float]:
    if fmt in (FMT_W8X8Y8Z8, FMT_RGBA8_UNORM):
        return tuple(v / 255.0 for v in data[off : off + 4])  # type: ignore[return-value]
    if fmt == FMT_W16NORM:
        a, b, c, d = struct.unpack_from("<hhhh", data, off)
        return (a / 32767.0, b / 32767.0, c / 32767.0, d / 32767.0)
    if fmt == FMT_XY32F:
        a, b = struct.unpack_from("<ff", data, off)
        return (a, b, 0.0, 0.0)
    return (1.0, 0.0, 0.0, 0.0)


def _buffer_bytes(buf: bytes, field_off: int | None) -> bytes:
    if field_off is None:
        return b""
    vec = fb.vector_offset(buf, field_off)
    ln = fb.vector_len(buf, vec)
    start = fb.vector_data(buf, vec)
    return buf[start : start + ln]


def _parse_trmbf(data: bytes) -> list[tuple[list[bytes], list[bytes]]]:
    root = fb.root_offset(data)
    fields = fb.table_fields(data, root).offsets
    mesh_bufs = fb.read_table_vector(data, fields[1])
    out: list[tuple[list[bytes], list[bytes]]] = []
    for mb in mesh_bufs:
        idx_tables = fb.read_table_vector(data, mb.offsets[0])
        vtx_tables = fb.read_table_vector(data, mb.offsets[1])
        idx_bufs = [_buffer_bytes(data, t.offsets[0]) for t in idx_tables]
        vtx_bufs = [_buffer_bytes(data, t.offsets[0]) for t in vtx_tables]
        out.append((idx_bufs, vtx_bufs))
    return out


def _element_size(fmt: int) -> int:
    if fmt in (FMT_XYZ32F,):
        return 12
    if fmt in (FMT_XY32F,):
        return 8
    if fmt in (FMT_W16FLOAT,):
        return 6
    if fmt in (FMT_W16NORM,):
        return 8
    if fmt in (FMT_W8X8Y8Z8, FMT_RGBA8_UNORM):
        return 4
    if fmt in (FMT_W32UINT, FMT_W32FLOAT):
        return 16
    return 0


def _mesh_stride(data: bytes, decl: fb.TableFields) -> int:
    sizes = fb.read_table_vector(data, decl.offsets[1]) if decl.offsets[1] else []
    if sizes and sizes[0].offsets[0]:
        return fb.i32(data, sizes[0].offsets[0])
    elems = fb.read_table_vector(data, decl.offsets[0])
    max_end = 0
    for el in elems:
        fmt = fb.i32(data, el.offsets[3]) if len(el.offsets) > 3 and el.offsets[3] else 0
        offset = fb.i32(data, el.offsets[4]) if len(el.offsets) > 4 and el.offsets[4] else 0
        max_end = max(max_end, offset + _element_size(fmt))
    return max_end


def _parse_mesh_part(
    data: bytes,
    mesh: fb.TableFields,
    decl_index: int,
    part: fb.TableFields,
    idx_buf: bytes,
    vtx_bufs: list[bytes],
) -> Submesh:
    decls = fb.read_table_vector(data, mesh.offsets[3])
    decl = decls[decl_index if decl_index < len(decls) else 0]
    elems = fb.read_table_vector(data, decl.offsets[0])
    sizes = fb.read_table_vector(data, decl.offsets[1])
    stride = _mesh_stride(data, decl)
    pos_layer = 0
    for el in elems:
        if len(el.offsets) > 1 and el.offsets[1] and fb.i32(data, el.offsets[1]) == USAGE_POSITION:
            pos_layer = fb.i32(data, el.offsets[2]) if len(el.offsets) > 2 and el.offsets[2] else 0
            break
    vbuf = vtx_bufs[pos_layer] if pos_layer < len(vtx_bufs) else (vtx_bufs[0] if vtx_bufs else b"")
    vcount = len(vbuf) // stride if stride else 0
    positions: list[tuple[float, float, float]] = [(0.0, 0.0, 0.0)] * vcount
    normals: list[tuple[float, float, float]] = [(0.0, 1.0, 0.0)] * vcount
    uvs: list[tuple[float, float]] = [(0.0, 0.0)] * vcount
    joints: list[tuple[int, int, int, int]] = [(0, 0, 0, 0)] * vcount
    weights: list[tuple[float, float, float, float]] = [(1.0, 0.0, 0.0, 0.0)] * vcount

    for el in elems:
        usage = fb.i32(data, el.offsets[1]) if len(el.offsets) > 1 and el.offsets[1] else 0
        layer = fb.i32(data, el.offsets[2]) if len(el.offsets) > 2 and el.offsets[2] else 0
        fmt = fb.i32(data, el.offsets[3]) if len(el.offsets) > 3 and el.offsets[3] else 0
        eoff = fb.i32(data, el.offsets[4]) if len(el.offsets) > 4 and el.offsets[4] else 0
        buf = vtx_bufs[layer] if layer < len(vtx_bufs) else vbuf
        for v in range(vcount):
            off = v * stride + eoff
            if usage == USAGE_POSITION:
                positions[v] = _read_vec3(buf, off, fmt)
            elif usage == USAGE_NORMAL:
                normals[v] = _read_vec3(buf, off, fmt)
            elif usage == USAGE_TEX_COORD:
                uvs[v] = _read_vec2(buf, off, fmt)
            elif usage == USAGE_BLEND_INDEX:
                joints[v] = _read_blend_indices(buf, off, fmt)
            elif usage == USAGE_BLEND_WEIGHTS:
                weights[v] = _read_blend_weights(buf, off, fmt)

    index_type = fb.i32(data, mesh.offsets[2]) if mesh.offsets[2] else INDEX_SHORT
    index_size = 1 << index_type
    start = fb.i32(data, part.offsets[1]) if part.offsets[1] else 0
    count = fb.i32(data, part.offsets[0]) if part.offsets[0] else 0
    base = start * index_size
    indices: list[int] = []
    for i in range(count):
        off = base + i * index_size
        if index_type == INDEX_BYTE:
            indices.append(idx_buf[off])
        elif index_type == INDEX_SHORT:
            indices.append(struct.unpack_from("<H", idx_buf, off)[0])
        else:
            indices.append(struct.unpack_from("<I", idx_buf, off)[0])

    mat = fb.read_string(data, part.offsets[3]) if part.offsets[3] else ""
    mesh_name = fb.read_string(data, mesh.offsets[0]) if mesh.offsets[0] else "mesh"
    return Submesh(
        name=mesh_name,
        material=mat,
        positions=positions,
        normals=normals,
        uvs=uvs,
        indices=indices,
        joints=joints,
        weights=weights,
    )


def _parse_trmsh(data: bytes, trmbf: bytes) -> list[Submesh]:
    root = fb.root_offset(data)
    fields = fb.table_fields(data, root).offsets
    meshes = fb.read_table_vector(data, fields[1])
    buffers = _parse_trmbf(trmbf)
    subs: list[Submesh] = []
    for i, mesh in enumerate(meshes):
        if i >= len(buffers):
            break
        idx_bufs, vtx_bufs = buffers[i]
        if not idx_bufs:
            continue
        parts = fb.read_table_vector(data, mesh.offsets[4])
        for part in parts:
            decl_i = fb.i32(data, part.offsets[4]) if part.offsets[4] else 0
            subs.append(_parse_mesh_part(data, mesh, decl_i, part, idx_bufs[0], vtx_bufs))
    return subs


def _parse_trskl(data: bytes) -> list[Bone]:
    root = fb.root_offset(data)
    fields = fb.table_fields(data, root).offsets
    nodes = fb.read_table_vector(data, fields[1])
    bones: list[Bone] = []
    for node in nodes:
        name = fb.read_string(data, node.offsets[0]) if node.offsets[0] else "bone"
        parent = fb.i32(data, node.offsets[4]) if node.offsets[4] else -1
        srt_table = fb.child_table(data, node.offsets[1])
        if srt_table is None:
            bones.append(Bone(name, parent, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (1.0, 1.0, 1.0)))
            continue
        srt_fields = srt_table.offsets
        scale = (1.0, 1.0, 1.0)
        rotate = (0.0, 0.0, 0.0)
        translate = (0.0, 0.0, 0.0)
        if len(srt_fields) > 0 and srt_fields[0]:
            scale = struct.unpack_from("<fff", data, srt_fields[0])
        if len(srt_fields) > 1 and srt_fields[1]:
            rotate = struct.unpack_from("<fff", data, srt_fields[1])
        if len(srt_fields) > 2 and srt_fields[2]:
            translate = struct.unpack_from("<fff", data, srt_fields[2])
        bones.append(Bone(name, parent, translate, rotate, scale))
    return bones


def _combine(base_dir: str, rel: str) -> str:
    rel = rel.replace("\\", "/")
    if rel.startswith("pokemon/") or rel.startswith("arc/"):
        return rel
    return str(PurePosixPath(base_dir) / rel)


def load_model(
    archive: TrinityArchive,
    trpak_path: str,
    trmdl_path: str,
) -> SwitchModel:
    """Load a TRMDL and its sibling TRMSH/TRMBF/TRSKL from a TRPAK."""
    base_dir = str(PurePosixPath(trmdl_path).parent)
    trmdl = archive.read_by_path(trpak_path, trmdl_path)
    root = fb.root_offset(trmdl)
    fields = fb.table_fields(trmdl, root).offsets
    mesh_tables = fb.read_table_vector(trmdl, fields[1])
    submeshes: list[Submesh] = []
    for mt in mesh_tables:
        rel = fb.read_string(trmdl, mt.offsets[0]) if mt.offsets[0] else ""
        msh_path = _combine(base_dir, rel)
        msh_data = archive.read_by_path(trpak_path, msh_path)
        msh_root = fb.root_offset(msh_data)
        msh_fields = fb.table_fields(msh_data, msh_root).offsets
        bf_rel = fb.read_string(msh_data, msh_fields[2]) if msh_fields[2] else ""
        bf_path = _combine(base_dir, bf_rel)
        trmbf = archive.read_by_path(trpak_path, bf_path)
        submeshes.extend(_parse_trmsh(msh_data, trmbf))

    bones: list[Bone] = []
    skel_field = fields[2]
    if skel_field is not None:
        skel_table = fb.child_table(trmdl, skel_field)
        if skel_table is not None and skel_table.offsets[0] is not None:
            skel_rel = fb.read_string(trmdl, skel_table.offsets[0])
            if skel_rel:
                skel_path = _combine(base_dir, skel_rel)
                bones = _parse_trskl(archive.read_by_path(trpak_path, skel_path))

    return SwitchModel(name=PurePosixPath(trmdl_path).stem, submeshes=submeshes, bones=bones)
