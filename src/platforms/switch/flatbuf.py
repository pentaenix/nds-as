"""Minimal Google FlatBuffer reader for Trinity archives (no code-gen)."""
from __future__ import annotations

import struct
from dataclasses import dataclass


def u8(buf: bytes, off: int) -> int:
    return buf[off]


def u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def i32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<i", buf, off)[0]


def u64(buf: bytes, off: int) -> int:
    return struct.unpack_from("<Q", buf, off)[0]


def u16(buf: bytes, off: int) -> int:
    return struct.unpack_from("<H", buf, off)[0]


@dataclass(slots=True)
class TableFields:
    offsets: list[int | None]

    def get(self, index: int) -> int | None:
        return self.offsets[index] if index < len(self.offsets) else None


def root_offset(buf: bytes) -> int:
    return u32(buf, 0)


def table_fields(buf: bytes, table_off: int) -> TableFields:
    vt = table_off - i32(buf, table_off)
    n = (u16(buf, vt) - 4) // 2
    fields: list[int | None] = []
    for i in range(n):
        rel = u16(buf, vt + 4 + i * 2)
        fields.append(table_off + rel if rel else None)
    return TableFields(offsets=fields)


def vector_offset(buf: bytes, field_off: int) -> int:
    return field_off + u32(buf, field_off)


def vector_len(buf: bytes, vec_off: int) -> int:
    return u32(buf, vec_off)


def vector_data(buf: bytes, vec_off: int) -> int:
    return vec_off + 4


def table_at(buf: bytes, vec_data: int, index: int, elem_size: int = 4) -> int:
    rel = u32(buf, vec_data + index * elem_size)
    return vec_data + index * elem_size + rel


def child_table(buf: bytes, field_pos: int | None) -> TableFields | None:
    if field_pos is None:
        return None
    return table_fields(buf, field_pos + u32(buf, field_pos))


def read_string(buf: bytes, field_off: int) -> str:
    stroff = field_off + u32(buf, field_off)
    length = u32(buf, stroff)
    return buf[stroff + 4 : stroff + 4 + length].decode("utf-8", "replace")


def read_u64_vector(buf: bytes, field_off: int | None) -> list[int]:
    if field_off is None:
        return []
    vec = vector_offset(buf, field_off)
    ln = vector_len(buf, vec)
    data = vector_data(buf, vec)
    return [u64(buf, data + i * 8) for i in range(ln)]


def read_u32_vector(buf: bytes, field_off: int | None) -> list[int]:
    if field_off is None:
        return []
    vec = vector_offset(buf, field_off)
    ln = vector_len(buf, vec)
    data = vector_data(buf, vec)
    return [u32(buf, data + i * 4) for i in range(ln)]


def read_table_vector(buf: bytes, field_off: int | None) -> list[TableFields]:
    if field_off is None:
        return []
    vec = vector_offset(buf, field_off)
    ln = vector_len(buf, vec)
    data = vector_data(buf, vec)
    out: list[TableFields] = []
    for i in range(ln):
        toff = table_at(buf, data, i)
        out.append(table_fields(buf, toff))
    return out


def read_string_vector(buf: bytes, field_off: int | None) -> list[str]:
    if field_off is None:
        return []
    vec = vector_offset(buf, field_off)
    ln = vector_len(buf, vec)
    data = vector_data(buf, vec)
    return [read_string(buf, data + i * 4) for i in range(ln)]
