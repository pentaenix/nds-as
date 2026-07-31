"""Decoder for Marine Park Empire's static V3D ``.SMO`` meshes.

The format has no public specification.  The layout implemented here is based
on invariants checked across the game's model catalog: version 500 files store
one or more triangle strips followed by 32-byte normal/position/UV vertices.
Version 50 files are deliberately rejected until their older layout is
characterized.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import re
import struct


_TEXTURE_RE = re.compile(
    # Some SMO records store the game's TGA extension as the two-byte ``.TG``
    # spelling. Texture resolution is stem-based, so retain the source spelling
    # while still recognizing it as a texture reference.
    rb"([A-Za-z0-9_ #.+-]{1,96}\.(?:bmp|tga|dds|tg))\x00",
    re.IGNORECASE,
)


class SmoDecodeError(ValueError):
    """Raised when a payload is not a supported Marine Park Empire SMO."""


@dataclass(frozen=True, slots=True)
class SmoMesh:
    vertices: tuple[tuple[float, float, float], ...]
    normals: tuple[tuple[float, float, float], ...]
    uvs: tuple[tuple[float, float], ...]
    faces: tuple[tuple[int, int, int], ...]
    face_materials: tuple[int, ...]
    texture_names: tuple[str, ...]
    material_slots: int
    warnings: tuple[str, ...] = ()


def _triangles_from_strip(indices: tuple[int, ...]) -> tuple[tuple[int, int, int], ...]:
    faces: list[tuple[int, int, int]] = []
    strip: list[int] = []
    parity = 0
    for index in indices:
        if index == 0xFFFF:
            strip.clear()
            parity = 0
            continue
        strip.append(index)
        if len(strip) < 3:
            continue
        a, b, c = strip[-3:]
        face = (a, b, c) if parity % 2 == 0 else (b, a, c)
        parity += 1
        if len(set(face)) == 3:
            faces.append(face)
    return tuple(faces)


def _texture_names(data: bytes) -> tuple[str, ...]:
    names: list[str] = []
    seen: set[str] = set()
    for match in _TEXTURE_RE.finditer(data):
        name = match.group(1).decode("ascii", errors="replace")
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        names.append(name)
    return tuple(names)


def decode_smo(data: bytes) -> SmoMesh:
    """Decode a version-500 static V3D mesh without modifying source data."""
    if len(data) < 0x20:
        raise SmoDecodeError("SMO payload is truncated")
    version, mesh_count, material_slots = struct.unpack_from("<III", data, 0)
    if version == 50:
        return _decode_smo_v50(data)
    if version != 500:
        raise SmoDecodeError(f"unsupported SMO version {version}; expected 500 or 50")

    if mesh_count <= 0 or mesh_count > 100_000:
        raise SmoDecodeError(f"invalid SMO mesh count {mesh_count}")
    cursor = 0x20
    normals: list[tuple[float, float, float]] = []
    vertices: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_materials: list[int] = []
    for mesh_index in range(mesh_count):
        if cursor + 0x58 > len(data):
            raise SmoDecodeError(f"SMO mesh {mesh_index} header is truncated")
        strip_field, vertex_count = struct.unpack_from("<II", data, cursor)
        if vertex_count <= 0 or vertex_count > 10_000_000:
            raise SmoDecodeError(f"invalid SMO vertex count {vertex_count}")
        # Counts + four RGBA material colors + mesh bounding sphere.
        cursor += 0x58
        # V3D stores four strip setup/teardown indices in addition to the field.
        index_count = strip_field + 4
        index_end = cursor + index_count * 2
        vertex_end = index_end + vertex_count * 32
        if vertex_end > len(data):
            raise SmoDecodeError(f"SMO mesh {mesh_index} extends beyond the payload")
        indices = struct.unpack_from(f"<{index_count}H", data, cursor)
        invalid = [index for index in indices if index != 0xFFFF and index >= vertex_count]
        if invalid:
            raise SmoDecodeError(
                f"SMO mesh {mesh_index} references vertex {invalid[0]} "
                f"but only {vertex_count} exist"
            )
        rows = struct.unpack_from(f"<{vertex_count * 8}f", data, index_end)
        if not all(math.isfinite(value) for value in rows):
            raise SmoDecodeError("SMO contains non-finite vertex values")
        vertex_base = len(vertices)
        for offset in range(0, len(rows), 8):
            normals.append((rows[offset], rows[offset + 1], rows[offset + 2]))
            vertices.append((rows[offset + 3], rows[offset + 4], rows[offset + 5]))
            uvs.append((rows[offset + 6], rows[offset + 7]))
        local_faces = _triangles_from_strip(indices)
        faces.extend(
            (a + vertex_base, b + vertex_base, c + vertex_base)
            for a, b, c in local_faces
        )
        face_materials.extend([mesh_index] * len(local_faces))
        cursor = vertex_end

    if not faces:
        raise SmoDecodeError("SMO triangle strip contains no visible faces")
    warnings: list[str] = []
    slots = max(int(mesh_count), int(material_slots))
    # A single texture shared by multiple mesh sections is a normal V3D layout,
    # not a damaged model. Only report genuinely irregular slot declarations.
    if material_slots not in {1, mesh_count}:
        warnings.append(
            f"source declares {mesh_count} meshes and {material_slots} texture slots"
        )
    return SmoMesh(
        vertices=tuple(vertices),
        normals=tuple(normals),
        uvs=tuple(uvs),
        faces=tuple(faces),
        face_materials=tuple(face_materials),
        texture_names=_texture_names(data[cursor:]),
        material_slots=slots,
        warnings=tuple(warnings),
    )


def _decode_smo_v50(data: bytes) -> SmoMesh:
    """Decode the older triangle-list layout used by five house decorations."""
    version, mesh_count, texture_count, flags = struct.unpack_from("<4I", data, 0)
    if version != 50 or flags != 0 or not 0 < mesh_count < 1024 or texture_count > 1024:
        raise SmoDecodeError("SMO v50 is an empty placeholder or has invalid counts")
    cursor = 0x10
    normals: list[tuple[float, float, float]] = []
    vertices: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_materials: list[int] = []
    for mesh_index in range(mesh_count):
        if cursor + 0x30 > len(data):
            raise SmoDecodeError(f"SMO v50 mesh {mesh_index} header is truncated")
        triangle_count, vertex_count = struct.unpack_from("<II", data, cursor)
        if not 0 < triangle_count < 10_000_000 or not 0 < vertex_count < 10_000_000:
            raise SmoDecodeError(f"SMO v50 mesh {mesh_index} counts are invalid")
        cursor += 0x30
        index_count = triangle_count * 3
        index_end = cursor + index_count * 2
        vertex_end = index_end + vertex_count * 32
        if vertex_end > len(data):
            raise SmoDecodeError(f"SMO v50 mesh {mesh_index} extends beyond the payload")
        indices = struct.unpack_from(f"<{index_count}H", data, cursor)
        if any(index >= vertex_count for index in indices):
            raise SmoDecodeError(f"SMO v50 mesh {mesh_index} references a missing vertex")
        rows = struct.unpack_from(f"<{vertex_count * 8}f", data, index_end)
        if not all(math.isfinite(value) for value in rows):
            raise SmoDecodeError("SMO v50 contains non-finite vertex values")
        vertex_base = len(vertices)
        for offset in range(0, len(rows), 8):
            normals.append((rows[offset], rows[offset + 1], rows[offset + 2]))
            vertices.append((rows[offset + 3], rows[offset + 4], rows[offset + 5]))
            uvs.append((rows[offset + 6], rows[offset + 7]))
        local_faces = [tuple(indices[offset:offset + 3]) for offset in range(0, index_count, 3)]
        visible = [face for face in local_faces if len(set(face)) == 3]
        faces.extend(tuple(index + vertex_base for index in face) for face in visible)
        face_materials.extend([mesh_index] * len(visible))
        cursor = vertex_end
    names: list[str] = []
    for _ in range(texture_count):
        if cursor + 24 > len(data):
            raise SmoDecodeError("SMO v50 texture table is truncated")
        name = data[cursor:cursor + 24].split(b"\0", 1)[0].decode("cp1252", errors="replace").strip()
        if name:
            names.append(name)
        cursor += 24
    if not faces:
        raise SmoDecodeError("SMO v50 contains no visible triangles")
    return SmoMesh(
        vertices=tuple(vertices), normals=tuple(normals), uvs=tuple(uvs),
        faces=tuple(faces), face_materials=tuple(face_materials),
        texture_names=tuple(names), material_slots=max(mesh_count, texture_count),
        warnings=("decoded legacy SMO v50 triangle-list layout",),
    )
