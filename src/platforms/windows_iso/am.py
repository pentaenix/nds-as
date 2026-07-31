"""Clean-room decoders for Marine Park Empire V3D animated assets.

AM1 stores skinned triangle-strip geometry, AM3 stores the bone hierarchy, and
AM2 stores named model-global matrix tracks.  The formats are byte-packed; no
alignment is assumed between mesh records.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct

import numpy as np


class AmDecodeError(ValueError):
    """Raised when an AM payload is truncated or fails structural checks."""


@dataclass(frozen=True, slots=True)
class Influence:
    bone: int
    weight: float
    position: tuple[float, float, float]
    normal: tuple[float, float, float]


@dataclass(frozen=True, slots=True)
class Am1Mesh:
    uvs: tuple[tuple[float, float], ...]
    influences: tuple[tuple[Influence, ...], ...]
    source_vertices: tuple[int, ...]
    faces: tuple[tuple[int, int, int], ...]
    face_materials: tuple[int, ...]
    texture_names: tuple[str, ...]
    bone_count: int


@dataclass(frozen=True, slots=True)
class Bone:
    bone_id: int
    name: str
    children: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class Skeleton:
    bones: tuple[Bone, ...]
    parents: tuple[int | None, ...]


@dataclass(frozen=True, slots=True)
class MatrixKey:
    time_ms: float
    matrix: np.ndarray


@dataclass(frozen=True, slots=True)
class Action:
    name: str
    end_ms: float


@dataclass(frozen=True, slots=True)
class AnimationSet:
    actions: tuple[Action, ...]
    duration_ms: float
    tracks: tuple[tuple[MatrixKey, ...], ...]

    def clips(self) -> tuple[tuple[str, float, float], ...]:
        """Return source action ranges from the engine's circular end table.

        AM2 stores the total duration beside the first action. Every following
        timestamp marks the end of the *previous* named action, and the final
        action occupies the tail up to that total duration.
        """
        if not self.actions:
            return (("animation", 0.0, self.duration_ms),)
        if len(self.actions) == 1:
            return ((self.actions[0].name or "animation", 0.0, self.actions[0].end_ms),)
        result: list[tuple[str, float, float]] = []
        for index in range(len(self.actions) - 1):
            start = 0.0 if index == 0 else self.actions[index].end_ms
            result.append((
                self.actions[index].name or f"action_{index}",
                start,
                self.actions[index + 1].end_ms,
            ))
        tail_start = self.actions[-1].end_ms
        tail_end = max(self.actions[0].end_ms, self.duration_ms)
        if tail_end > tail_start:
            result.append((
                self.actions[-1].name or f"action_{len(self.actions) - 1}",
                tail_start,
                tail_end,
            ))
        return tuple(result)


def animation_clips_in_export_order(
    animation_sets: tuple[AnimationSet, ...] | list[AnimationSet],
) -> tuple[tuple[AnimationSet, str, float, float], ...]:
    """Return uniquely named clips with a healthy idle first when available."""
    source: list[tuple[int, AnimationSet, str, float, float]] = []
    for animation in animation_sets:
        for name, start_ms, end_ms in animation.clips():
            if end_ms > start_ms:
                source.append((len(source), animation, name, start_ms, end_ms))

    def rank(row: tuple[int, AnimationSet, str, float, float]) -> tuple[int, int]:
        name = row[2].casefold()
        unhealthy = any(word in name for word in ("sick", "injur", "sleep", "dead"))
        return (0 if "idle" in name and not unhealthy else 1 if "idle" in name else 2, row[0])

    used_names: dict[str, int] = {}
    result: list[tuple[AnimationSet, str, float, float]] = []
    for _, animation, source_name, start_ms, end_ms in sorted(source, key=rank):
        occurrence = used_names.get(source_name.casefold(), 0)
        used_names[source_name.casefold()] = occurrence + 1
        name = source_name if occurrence == 0 else f"{source_name}_{occurrence + 1}"
        result.append((animation, name, start_ms, end_ms))
    return tuple(result)


def _strip_faces(indices: tuple[int, ...]) -> tuple[tuple[int, int, int], ...]:
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
        if len({a, b, c}) == 3:
            faces.append(face)
    return tuple(faces)


def _cstring(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("cp1252", errors="replace").strip()


@dataclass(slots=True)
class _VertexState:
    cursor: int
    previous: "_VertexState | None"
    uv: tuple[float, float] | None = None
    source: int = 0
    influences: tuple[Influence, ...] = ()


def _vertex_candidates(data: bytes, cursor: int, bone_count: int) -> list[_VertexState]:
    if cursor + 16 > len(data):
        return []
    u, v, adjacency_count = struct.unpack_from("<2fI", data, cursor)
    if not math.isfinite(u + v) or abs(u) > 1e6 or abs(v) > 1e6 or adjacency_count > 1_000_000:
        return []
    influence_cursor = cursor + 12 + adjacency_count * 4
    if influence_cursor + 4 > len(data):
        return []
    source_vertex = struct.unpack_from("<I", data, influence_cursor)[0]
    influence_cursor += 4
    influences: list[Influence] = []
    total_weight = 0.0
    candidates: list[_VertexState] = []
    # Most vertices have at most four influences, but several terrestrial
    # animals retain many very small weights. Preserve up to sixteen rather
    # than silently discarding data; glTF writes additional JOINTS_n sets.
    for _ in range(16):
        if influence_cursor + 32 > len(data):
            break
        values = struct.unpack_from("<If6f", data, influence_cursor)
        bone, weight = int(values[0]), float(values[1])
        vectors = values[2:]
        if (bone >= bone_count or not math.isfinite(weight) or weight < 0.0 or
                weight > 1.001 or not all(math.isfinite(value) for value in vectors)):
            break
        influences.append(Influence(bone, weight, tuple(vectors[:3]), tuple(vectors[3:])))
        total_weight += weight
        influence_cursor += 32
        # Some assets retain zero-weight records after reaching 1.0, so every
        # structurally valid stopping point is kept until the next vertex proves it.
        if 0.98 <= total_weight <= 1.02 or total_weight == 0.0:
            stored = tuple(influences)
            if total_weight == 0.0:
                # A few source meshes contain vertices whose retained records
                # are all zero-weight. Keep their first bone-local position so
                # the vertex remains usable by standard skinning formats.
                first = influences[0]
                stored = (Influence(first.bone, 1.0, first.position, first.normal),
                          *tuple(influences[1:]))
            candidates.append(_VertexState(
                influence_cursor, None, (u, v), source_vertex, stored
            ))
    return candidates


def _vertex_stream(data: bytes, start: int, count: int, bone_count: int) -> list[_VertexState]:
    states: dict[int, _VertexState] = {start: _VertexState(start, None)}
    for _ in range(count):
        next_states: dict[int, _VertexState] = {}
        for state in states.values():
            for candidate in _vertex_candidates(data, state.cursor, bone_count):
                candidate.previous = state
                next_states.setdefault(candidate.cursor, candidate)
        if not next_states:
            return []
        # Valid files normally have one state. This cap prevents corrupt files
        # filled with zeros from causing an unbounded combinatorial search.
        if len(next_states) > 256:
            next_states = dict(sorted(next_states.items())[:256])
        states = next_states
    return list(states.values())


def _unwind_vertices(state: _VertexState, count: int) -> tuple[list[tuple[float, float]], list[int], list[tuple[Influence, ...]]]:
    rows: list[_VertexState] = []
    current: _VertexState | None = state
    while current is not None and current.uv is not None:
        rows.append(current)
        current = current.previous
    rows.reverse()
    if len(rows) != count:
        raise AmDecodeError("AM1 vertex-state reconstruction failed")
    return ([row.uv for row in rows if row.uv is not None],
            [row.source for row in rows], [row.influences for row in rows])


def decode_am1(data: bytes) -> Am1Mesh:
    """Decode version-500 skinned geometry and per-bone local attributes."""
    if len(data) < 0x24:
        raise AmDecodeError("AM1 payload is truncated")
    version, bone_count, mesh_count, texture_count, unknown = struct.unpack_from("<5I", data, 0)
    if version != 500:
        raise AmDecodeError(f"unsupported AM1 version {version}; expected 500")
    if not 0 < bone_count < 65536 or not 0 < mesh_count < 1024 or texture_count > 1024:
        raise AmDecodeError("AM1 header counts are implausible")
    if unknown != 0:
        raise AmDecodeError(f"unsupported AM1 flags {unknown}")

    cursor = 0x24
    all_uvs: list[tuple[float, float]] = []
    all_influences: list[tuple[Influence, ...]] = []
    all_sources: list[int] = []
    all_faces: list[tuple[int, int, int]] = []
    face_materials: list[int] = []
    for mesh_index in range(mesh_count):
        if cursor + 0x48 > len(data):
            raise AmDecodeError(f"AM1 mesh {mesh_index} header is truncated")
        encoded_indices, vertex_count = struct.unpack_from("<II", data, cursor)
        if not encoded_indices or not 0 < vertex_count < 10_000_000:
            raise AmDecodeError(f"AM1 mesh {mesh_index} counts are invalid")
        # AM1 has a 0x48-byte material/bounds header. The strip begins with
        # eight setup indices immediately after it, and its count field omits
        # four setup/teardown entries. Treating the header as 0x58 happened to
        # preserve the vertex boundary but silently dropped the first eight
        # indices, leaving two visible triangles missing from many animals.
        cursor += 0x48
        index_count = encoded_indices + 4
        index_end = cursor + index_count * 2
        if index_end > len(data):
            raise AmDecodeError(f"AM1 mesh {mesh_index} index strip is truncated")
        indices = struct.unpack_from(f"<{index_count}H", data, cursor)
        invalid = [value for value in indices if value != 0xFFFF and value >= vertex_count]
        if invalid:
            raise AmDecodeError(f"AM1 mesh {mesh_index} references missing vertex {invalid[0]}")
        cursor = index_end
        vertex_base = len(all_uvs)
        possibilities = _vertex_stream(data, cursor, vertex_count, bone_count)
        if not possibilities:
            raise AmDecodeError(f"AM1 mesh {mesh_index} vertex stream is invalid")
        if mesh_index + 1 < mesh_count:
            plausible = [state for state in possibilities if state.cursor + 0x48 <= len(data)
                         and struct.unpack_from("<I", data, state.cursor)[0] >= 4
                         and 0 < struct.unpack_from("<I", data, state.cursor + 4)[0] < 10_000_000]
        else:
            plausible = [state for state in possibilities if state.cursor + texture_count * 24 <= len(data)]
        if not plausible:
            raise AmDecodeError(f"AM1 mesh {mesh_index} has no valid record boundary")
        # Extra zero-weight influence blocks make later boundaries preferable.
        state = max(plausible, key=lambda item: item.cursor)
        mesh_uvs, mesh_sources, mesh_influences = _unwind_vertices(state, vertex_count)
        all_uvs.extend(mesh_uvs)
        all_sources.extend(mesh_sources)
        all_influences.extend(mesh_influences)
        cursor = state.cursor
        local_faces = _strip_faces(indices)
        all_faces.extend((a + vertex_base, b + vertex_base, c + vertex_base) for a, b, c in local_faces)
        face_materials.extend([mesh_index] * len(local_faces))

    texture_names: list[str] = []
    for _ in range(texture_count):
        if cursor + 24 > len(data):
            raise AmDecodeError("AM1 texture table is truncated")
        texture_names.append(_cstring(data[cursor:cursor + 24]))
        cursor += 24
    if not all_faces:
        raise AmDecodeError("AM1 triangle strips contain no visible faces")
    return Am1Mesh(
        uvs=tuple(all_uvs), influences=tuple(all_influences),
        source_vertices=tuple(all_sources), faces=tuple(all_faces),
        face_materials=tuple(face_materials), texture_names=tuple(texture_names),
        bone_count=bone_count,
    )


def decode_am3(data: bytes) -> Skeleton:
    if len(data) < 4:
        raise AmDecodeError("AM3 payload is truncated")
    bone_count = struct.unpack_from("<I", data, 0)[0]
    if not 0 < bone_count < 65536:
        raise AmDecodeError(f"invalid AM3 bone count {bone_count}")
    cursor = 4
    bones: list[Bone] = []
    for index in range(bone_count):
        if cursor + 58 > len(data):
            raise AmDecodeError(f"AM3 bone {index} is truncated")
        bone_id = struct.unpack_from("<I", data, cursor)[0]
        name = _cstring(data[cursor + 4:cursor + 54]) or f"bone_{bone_id}"
        child_count = struct.unpack_from("<I", data, cursor + 54)[0]
        cursor += 58
        if child_count > bone_count or cursor + child_count * 4 > len(data):
            raise AmDecodeError(f"AM3 bone {bone_id} child list is invalid")
        children = struct.unpack_from(f"<{child_count}I", data, cursor) if child_count else ()
        cursor += child_count * 4
        if bone_id >= bone_count or any(child >= bone_count for child in children):
            raise AmDecodeError("AM3 bone ID is outside the skeleton")
        bones.append(Bone(int(bone_id), name, tuple(int(child) for child in children)))
    by_id = {bone.bone_id: bone for bone in bones}
    if len(by_id) != bone_count:
        raise AmDecodeError("AM3 contains duplicate bone IDs")
    ordered = tuple(by_id[index] for index in range(bone_count))
    parents: list[int | None] = [None] * bone_count
    for bone in ordered:
        for child in bone.children:
            if parents[child] is not None:
                raise AmDecodeError(f"AM3 bone {child} has multiple parents")
            parents[child] = bone.bone_id
    return Skeleton(ordered, tuple(parents))


def decode_am2(data: bytes) -> AnimationSet:
    if len(data) < 12:
        raise AmDecodeError("AM2 payload is truncated")
    bone_count, action_count, flags = struct.unpack_from("<3I", data, 0)
    if not 0 < bone_count < 65536 or action_count > 4096 or flags != 0:
        raise AmDecodeError("AM2 header is invalid")
    cursor = 12
    actions: list[Action] = []
    for index in range(action_count):
        if cursor + 62 > len(data):
            raise AmDecodeError(f"AM2 action {index} is truncated")
        end_ms = struct.unpack_from("<f", data, cursor)[0]
        actions.append(Action(_cstring(data[cursor + 4:cursor + 62]) or f"action_{index}", end_ms))
        cursor += 62
    if cursor + 4 > len(data):
        raise AmDecodeError("AM2 duration is truncated")
    duration_ms = struct.unpack_from("<f", data, cursor)[0]
    cursor += 4
    tracks: list[tuple[MatrixKey, ...]] = []
    for bone in range(bone_count):
        if cursor + 4 > len(data):
            raise AmDecodeError(f"AM2 track {bone} is truncated")
        key_count = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        if not key_count or key_count > 10_000_000 or cursor + key_count * 52 > len(data):
            raise AmDecodeError(f"AM2 track {bone} key count is invalid")
        keys: list[MatrixKey] = []
        previous = -math.inf
        for _ in range(key_count):
            values = struct.unpack_from("<13f", data, cursor)
            cursor += 52
            time_ms = float(values[0])
            if not all(math.isfinite(value) for value in values) or time_ms < previous:
                raise AmDecodeError(f"AM2 track {bone} has invalid key data")
            previous = time_ms
            matrix = np.eye(4, dtype=np.float64)
            matrix[:3, :4] = np.asarray(values[1:], dtype=np.float64).reshape(3, 4)
            keys.append(MatrixKey(time_ms, matrix))
        tracks.append(tuple(keys))
    if cursor != len(data) and not _valid_am2_footer(data[cursor:]):
        raise AmDecodeError(f"AM2 has {len(data) - cursor} unexplained trailing bytes")
    return AnimationSet(tuple(actions), float(duration_ms), tuple(tracks))


def _valid_am2_footer(data: bytes) -> bool:
    """Recognize the zero-filled auxiliary footer used by butterflies/trains."""
    if len(data) < 24 or len(data) % 4:
        return False
    words = struct.unpack(f"<{len(data) // 4}I", data)
    version, item_count = words[:2]
    return version == 1 and len(data) == 20 + item_count * 4 and not any(words[2:])
