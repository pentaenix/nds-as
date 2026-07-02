"""Game Freak GFL2 container parsing (GFModel / GFTexture) used by Pokémon
X/Y through Ultra Sun/Ultra Moon.

Layouts follow the public SPICA research (gdkchan/SPICA, Unlicense).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from .pica import (
    GPUREG_ATTRIBBUFFER0_CONFIG1,
    GPUREG_ATTRIBBUFFER0_CONFIG2,
    GPUREG_ATTRIBBUFFERS_FORMAT_HIGH,
    GPUREG_ATTRIBBUFFERS_FORMAT_LOW,
    GPUREG_INDEXBUFFER_CONFIG,
    GPUREG_NUMVERTICES,
    GPUREG_PRIMITIVE_CONFIG,
    GPUREG_VSH_ATTRIBUTES_PERMUTATION_HIGH,
    GPUREG_VSH_ATTRIBUTES_PERMUTATION_LOW,
    GPUREG_VSH_NUM_ATTR,
    read_pica_commands,
)

GFMODEL_MAGIC = 0x15122117
GFTEXTURE_MAGIC = 0x15041213

# GFTextureFormat -> internal PICA format index (see pica.decode_pica_texture).
GF_TEXTURE_FORMATS: dict[int, int] = {
    0x02: 3,   # RGB565
    0x03: 1,   # RGB8
    0x04: 0,   # RGBA8
    0x16: 4,   # RGBA4
    0x17: 2,   # RGBA5551
    0x23: 5,   # LA8
    0x24: 6,   # HILO8
    0x25: 7,   # L8
    0x26: 8,   # A8
    0x27: 9,   # LA4
    0x28: 10,  # L4
    0x29: 11,  # A4
    0x2A: 12,  # ETC1
    0x2B: 13,  # ETC1A4
}

# PICA vertex attribute names (permutation nibbles).
ATTR_POSITION = 0
ATTR_NORMAL = 1
ATTR_TANGENT = 2
ATTR_COLOR = 3
ATTR_TEXCOORD0 = 4
ATTR_TEXCOORD1 = 5
ATTR_TEXCOORD2 = 6
ATTR_BONE_INDEX = 7
ATTR_BONE_WEIGHT = 8

_ATTR_SCALES = (1.0 / 127.0, 1.0 / 255.0, 1.0 / 32767.0, 1.0)
_ATTR_SIZES = (1, 1, 2, 4)  # sbyte, ubyte, short, float


class GfParseError(ValueError):
    pass


class _Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0):
        self.data = data
        self.pos = pos

    def u8(self) -> int:
        v = self.data[self.pos]
        self.pos += 1
        return v

    def u16(self) -> int:
        v = struct.unpack_from("<H", self.data, self.pos)[0]
        self.pos += 2
        return v

    def u32(self) -> int:
        v = struct.unpack_from("<I", self.data, self.pos)[0]
        self.pos += 4
        return v

    def i32(self) -> int:
        v = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return v

    def f32(self) -> float:
        v = struct.unpack_from("<f", self.data, self.pos)[0]
        self.pos += 4
        return v

    def bytes(self, count: int) -> bytes:
        v = self.data[self.pos : self.pos + count]
        self.pos += count
        return v

    def skip(self, count: int) -> None:
        self.pos += count

    def align16(self) -> None:
        if self.pos & 0xF:
            self.pos += 0x10 - (self.pos & 0xF)

    def padded_string(self, size: int) -> str:
        raw = self.bytes(size)
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace")

    def byte_length_string(self) -> str:
        return self.padded_string_exact(self.u8())

    def padded_string_exact(self, size: int) -> str:
        raw = self.bytes(size)
        return raw.split(b"\x00", 1)[0].decode("ascii", "replace")

    def hash_name(self) -> str:
        self.u32()  # FNV1 hash
        return self.byte_length_string()

    def section(self) -> tuple[str, int]:
        magic = self.padded_string_exact(8)
        length = self.u32()
        self.u32()  # 0xffffffff padding
        return magic, length


# -- data model --------------------------------------------------------------


@dataclass(slots=True)
class GfTexture:
    name: str
    width: int
    height: int
    gf_format: int
    pica_format: int
    raw: bytes

    def decode_rgba(self) -> bytes:
        from .pica import decode_pica_texture

        return decode_pica_texture(self.raw, self.width, self.height, self.pica_format)

    def to_png(self) -> bytes:
        from .pica import rgba_to_png

        return rgba_to_png(self.decode_rgba(), self.width, self.height)


@dataclass(slots=True)
class GfBone:
    name: str
    parent: str
    flags: int
    scale: tuple[float, float, float]
    rotation: tuple[float, float, float]
    translation: tuple[float, float, float]


@dataclass(slots=True)
class GfTextureUnit:
    name: str
    unit_index: int
    scale: tuple[float, float]
    rotation: float
    translation: tuple[float, float]
    wrap_u: int = 2  # 0 clamp-edge, 1 clamp-border, 2 repeat, 3 mirror
    wrap_v: int = 2


@dataclass(slots=True)
class GfMaterial:
    name: str
    texture_names: list[str] = field(default_factory=list)
    texture_units: list[GfTextureUnit] = field(default_factory=list)


@dataclass(slots=True)
class GfSubMesh:
    material_name: str
    positions: list[tuple[float, float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    uvs: list[tuple[float, float]] = field(default_factory=list)
    indices: list[int] = field(default_factory=list)
    # Skinning: per-submesh table mapping the local bone-index attribute value
    # to a skeleton bone index, plus decoded per-vertex indices/weights.
    bone_table: list[int] = field(default_factory=list)
    joints: list[tuple[int, int, int, int]] = field(default_factory=list)
    weights: list[tuple[float, float, float, float]] = field(default_factory=list)


@dataclass(slots=True)
class GfMesh:
    name: str
    submeshes: list[GfSubMesh] = field(default_factory=list)


@dataclass(slots=True)
class GfModel:
    name: str
    texture_names: list[str] = field(default_factory=list)
    material_names: list[str] = field(default_factory=list)
    materials: list[GfMaterial] = field(default_factory=list)
    bones: list[GfBone] = field(default_factory=list)
    meshes: list[GfMesh] = field(default_factory=list)


# -- GFTexture ---------------------------------------------------------------


def is_gf_texture(data: bytes) -> bool:
    return len(data) >= 4 and struct.unpack_from("<I", data)[0] == GFTEXTURE_MAGIC


def parse_gf_texture(data: bytes) -> GfTexture:
    r = _Reader(data)
    if r.u32() != GFTEXTURE_MAGIC:
        raise GfParseError("not a GFTexture")
    r.u32()  # texture count (always 1 per container)
    r.section()  # "texture"
    texture_length = r.u32()
    r.skip(0x0C)
    name = r.padded_string(0x40)
    width = r.u16()
    height = r.u16()
    gf_format = r.u16()
    r.u16()  # mipmap size
    r.skip(0x10)
    raw = r.bytes(texture_length)
    pica_format = GF_TEXTURE_FORMATS.get(gf_format)
    if pica_format is None:
        raise GfParseError(f"unknown GFTexture format 0x{gf_format:X}")
    return GfTexture(name=name, width=width, height=height, gf_format=gf_format, pica_format=pica_format, raw=raw)


# -- GFModel -----------------------------------------------------------------


def is_gf_model(data: bytes) -> bool:
    return len(data) >= 4 and struct.unpack_from("<I", data)[0] == GFMODEL_MAGIC


def _read_hash_table(r: _Reader) -> list[str]:
    count = r.u32()
    names: list[str] = []
    for _ in range(count):
        r.u32()  # hash
        names.append(r.padded_string(0x40))
    return names


def parse_gf_model(data: bytes, name: str = "model") -> GfModel:
    r = _Reader(data)
    if r.u32() != GFMODEL_MAGIC:
        raise GfParseError("not a GFModel")
    r.u32()  # sections count
    r.align16()
    r.section()  # "gfmodel"

    _shader_names = _read_hash_table(r)
    texture_names = _read_hash_table(r)
    material_names = _read_hash_table(r)
    mesh_names = _read_hash_table(r)

    r.skip(0x10 * 2)  # bbox min/max
    r.skip(0x40)      # transform 4x4
    unk_length = r.u32()
    unk_offset = r.u32()
    r.skip(8)
    r.skip(unk_offset + unk_length)

    bone_count = r.i32()
    r.skip(0x0C)
    bones: list[GfBone] = []
    for _ in range(bone_count):
        bname = r.byte_length_string()
        parent = r.byte_length_string()
        flags = r.u8()
        scale = (r.f32(), r.f32(), r.f32())
        rot = (r.f32(), r.f32(), r.f32())
        trans = (r.f32(), r.f32(), r.f32())
        bones.append(GfBone(bname, parent, flags, scale, rot, trans))
    r.align16()

    lut_count = r.i32()
    lut_length = r.i32()
    r.align16()
    for _ in range(lut_count):
        r.skip(0x10 + lut_length)

    materials: list[GfMaterial] = []
    for _ in material_names:
        materials.append(_parse_material(r))

    meshes: list[GfMesh] = []
    for mesh_name in mesh_names:
        meshes.append(_parse_mesh(r, mesh_name))

    return GfModel(
        name=name,
        texture_names=texture_names,
        material_names=material_names,
        materials=materials,
        bones=bones,
        meshes=meshes,
    )


def _parse_material(r: _Reader) -> GfMaterial:
    _magic, length = r.section()
    end = r.pos + length
    material_name = r.hash_name()
    r.hash_name()  # shader name
    r.hash_name()  # vertex shader name
    r.hash_name()  # fragment shader name

    r.skip(3 * 4)  # LUT hashes
    r.skip(4)      # padding
    r.skip(1)      # bump texture
    r.skip(6)      # constant assignments
    r.skip(1)      # padding
    r.skip(12 * 4)  # 12 RGBA colors
    r.skip(4 * 4)  # edge type / id-edge / edge id / projection type
    r.skip(4 * 4)  # rim/phong pow+scale
    r.skip(2 * 4)  # id edge offset enable / edge map alpha mask
    r.skip(9 * 4)  # bake textures + constants
    r.skip(4)      # vertex shader type
    r.skip(4 * 4)  # shader params

    units_count = r.u32()
    texture_names: list[str] = []
    texture_units: list[GfTextureUnit] = []
    for _ in range(units_count):
        tex_name = r.hash_name()
        texture_names.append(tex_name)
        unit_index = r.u8()
        r.skip(1)  # mapping type
        scale = (r.f32(), r.f32())
        rotation = r.f32()
        translation = (r.f32(), r.f32())
        wrap_u = r.u32()
        wrap_v = r.u32()
        r.skip(2 * 4)  # mag/min filter
        r.skip(4)      # min LOD
        texture_units.append(
            GfTextureUnit(
                name=tex_name,
                unit_index=unit_index,
                scale=scale,
                rotation=rotation,
                translation=translation,
                wrap_u=wrap_u,
                wrap_v=wrap_v,
            )
        )

    # Skip the GPU command block; material section length covers everything.
    r.pos = end
    return GfMaterial(name=material_name, texture_names=texture_names, texture_units=texture_units)


def _parse_mesh(r: _Reader, mesh_name: str) -> GfMesh:
    _magic, length = r.section()
    start = r.pos
    end = start + length

    r.u32()  # name hash
    r.padded_string(0x40)
    r.u32()
    r.skip(0x10 * 2)  # bbox
    submesh_count = r.u32()
    r.i32()  # bone indices per vertex
    r.skip(0x10)

    command_blocks: list[list[int]] = []
    while True:
        commands_length = r.u32()
        command_index = r.u32()
        commands_count = r.u32()
        r.u32()  # padding
        words = list(struct.unpack_from(f"<{commands_length >> 2}I", r.data, r.pos))
        r.skip(commands_length)
        command_blocks.append(words)
        if command_index >= commands_count - 1:
            break

    names: list[str] = []
    sizes: list[tuple[int, int, int, int]] = []
    bone_tables: list[list[int]] = []
    for _ in range(submesh_count):
        r.u32()  # submesh name hash
        names.append(r.padded_string_exact(r.u32()))
        bone_indices_count = r.u8()
        bone_tables.append(list(r.bytes(0x1F))[:bone_indices_count])
        vertices_count = r.i32()
        indices_count = r.i32()
        vertices_length = r.i32()
        indices_length = r.i32()
        sizes.append((vertices_count, indices_count, vertices_length, indices_length))

    mesh = GfMesh(name=mesh_name)
    for sub_index in range(submesh_count):
        enable_cmds = command_blocks[sub_index * 3 + 0]
        index_cmds = command_blocks[sub_index * 3 + 2]
        vertices_count, _indices_count, vertices_length, indices_length = sizes[sub_index]

        buffer_formats = 0
        buffer_attributes = 0
        buffer_permutation = 0
        attributes_total = 0
        vertex_stride = 0
        for register, param in read_pica_commands(enable_cmds):
            if register == GPUREG_ATTRIBBUFFERS_FORMAT_LOW:
                buffer_formats |= param
            elif register == GPUREG_ATTRIBBUFFERS_FORMAT_HIGH:
                buffer_formats |= param << 32
            elif register == GPUREG_ATTRIBBUFFER0_CONFIG1:
                buffer_attributes |= param
            elif register == GPUREG_ATTRIBBUFFER0_CONFIG2:
                buffer_attributes |= (param & 0xFFFF) << 32
                vertex_stride = (param >> 16) & 0xFF
            elif register == GPUREG_VSH_NUM_ATTR:
                attributes_total = param + 1
            elif register == GPUREG_VSH_ATTRIBUTES_PERMUTATION_LOW:
                buffer_permutation |= param
            elif register == GPUREG_VSH_ATTRIBUTES_PERMUTATION_HIGH:
                buffer_permutation |= param << 32

        # (name, format, elements, scale, byte offset)
        attributes: list[tuple[int, int, int, float, int]] = []
        offset = 0
        for idx in range(attributes_total):
            if (buffer_formats >> (48 + idx)) & 1:
                continue  # fixed attribute — no per-vertex data
            permutation_idx = (buffer_attributes >> (idx * 4)) & 0xF
            attr_name = (buffer_permutation >> (permutation_idx * 4)) & 0xF
            attr_fmt_raw = (buffer_formats >> (permutation_idx * 4)) & 0xF
            fmt = attr_fmt_raw & 3
            elements = (attr_fmt_raw >> 2) + 1
            scale = 1.0 if attr_name == ATTR_BONE_INDEX else _ATTR_SCALES[fmt]
            attributes.append((attr_name, fmt, elements, scale, offset))
            offset += _ATTR_SIZES[fmt] * elements

        index_is_16bit = False
        index_count = 0
        for register, param in read_pica_commands(index_cmds):
            if register == GPUREG_INDEXBUFFER_CONFIG:
                index_is_16bit = (param >> 31) != 0
            elif register == GPUREG_NUMVERTICES:
                index_count = param
            elif register == GPUREG_PRIMITIVE_CONFIG:
                pass  # triangles for all Pokémon meshes

        raw_vertices = r.bytes(vertices_length)
        index_pos = r.pos
        if index_is_16bit:
            indices = list(struct.unpack_from(f"<{index_count}H", r.data, index_pos))
        else:
            indices = list(r.data[index_pos : index_pos + index_count])
        r.pos = index_pos + indices_length

        sub = GfSubMesh(
            material_name=names[sub_index],
            indices=indices,
            bone_table=bone_tables[sub_index],
        )
        stride = vertex_stride or offset
        if stride:
            _decode_vertices(sub, raw_vertices, stride, vertices_count, attributes)
        mesh.submeshes.append(sub)

    r.pos = end
    return mesh


def _decode_vertices(
    sub: GfSubMesh,
    raw: bytes,
    stride: int,
    count: int,
    attributes: list[tuple[int, int, int, float, int]],
) -> None:
    unpackers = {0: "b", 1: "B", 2: "h", 3: "f"}
    if count <= 0 and stride:
        count = len(raw) // stride
    has_vertex_skinning = any(
        a[0] in (ATTR_BONE_INDEX, ATTR_BONE_WEIGHT) for a in attributes
    )
    for vi in range(count):
        base = vi * stride
        joints: tuple[int, int, int, int] | None = None
        weights: tuple[float, float, float, float] | None = None
        for attr_name, fmt, elements, scale, attr_offset in attributes:
            if attr_name not in (
                ATTR_POSITION,
                ATTR_NORMAL,
                ATTR_TEXCOORD0,
                ATTR_BONE_INDEX,
                ATTR_BONE_WEIGHT,
            ):
                continue
            values = struct.unpack_from(f"<{elements}{unpackers[fmt]}", raw, base + attr_offset)
            scaled = tuple(v * scale for v in values)
            if attr_name == ATTR_POSITION:
                sub.positions.append((scaled[0], scaled[1], scaled[2] if elements > 2 else 0.0))
            elif attr_name == ATTR_NORMAL:
                sub.normals.append((scaled[0], scaled[1], scaled[2] if elements > 2 else 0.0))
            elif attr_name == ATTR_TEXCOORD0:
                sub.uvs.append((scaled[0], scaled[1] if elements > 1 else 0.0))
            elif attr_name == ATTR_BONE_INDEX:
                raw_idx = tuple(int(v) for v in values) + (0, 0, 0)
                joints = raw_idx[:4]
            elif attr_name == ATTR_BONE_WEIGHT:
                padded = scaled + (0.0, 0.0, 0.0)
                weights = padded[:4]
        if has_vertex_skinning:
            sub.joints.append(joints or (0, 0, 0, 0))
            sub.weights.append(weights or (0.0, 0.0, 0.0, 0.0))
        elif sub.bone_table:
            # Single-bone submesh (bone index/weight are fixed attributes):
            # every vertex binds rigidly to the first table entry.
            sub.joints.append((0, 0, 0, 0))
            sub.weights.append((1.0, 0.0, 0.0, 0.0))
