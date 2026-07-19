"""Game Freak GFMotion (Gen6/Gen7 Pokémon skeletal animation) parsing.

Layout follows the public SPICA research (gdkchan/SPICA, Unlicense) and was
verified byte-by-byte against Pokémon Ultra Moon `/a/0/9/4` animation packs:

  u32 magic (0x00060000)
  u32 section count
  sections[count]: { u32 kind, u32 length, u32 address }   (addresses are
                                                            file-relative)
  kind 0 (sub header): u32 frames_count, u16 loop flag, u16 blend flag,
                       float3 region min, float3 region max, u32 hash
  kind 1 (skeletal):   i32 bone_names_count, u32 bone_names_length,
                       bone_names_length bytes of u8-length-prefixed names,
                       then per name one bone transform block:
                         u32 flags, u32 length, 9 tracks (SX SY SZ RX RY RZ
                         TX TY TZ), 3 bits of *flags* per track:
                           0     -> no data (bind pose)
                           3     -> single constant float
                           4     -> keyframe list, quantized u16 keys
                           5     -> keyframe list, float32 keys
                       flags bit 31 clear -> rotation tracks store a halved
                       axis-angle vector; set -> Euler XYZ radians.
"""
from __future__ import annotations

import math
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Iterable

GFMOTION_MAGIC = 0x00060000

_SECT_SUBHEADER = 0
_SECT_SKELETAL = 1
_SECT_MATERIAL = 3
_SECT_MATERIAL_ALT = 5
_SECT_VISIBILITY = 6

# GF Pokémon eye/iris expression sheets are 2 columns × 4 rows.
EYE_SHEET_COLS = 2
EYE_SHEET_ROWS = 4

# Pokémon 3DS motions play at 30 frames per second.
FRAME_RATE = 30.0


class GfMotionError(ValueError):
    pass


@dataclass(slots=True)
class GfMotKey:
    frame: float
    value: float
    slope: float


@dataclass(slots=True)
class GfMotBoneTrack:
    name: str
    is_axis_angle: bool
    # 9 channels: SX SY SZ RX RY RZ TX TY TZ; empty list = bind pose.
    channels: list[list[GfMotKey]] = field(default_factory=lambda: [[] for _ in range(9)])

    @property
    def has_scale(self) -> bool:
        return any(self.channels[i] for i in range(0, 3))

    @property
    def has_rotation(self) -> bool:
        return any(self.channels[i] for i in range(3, 6))

    @property
    def has_translation(self) -> bool:
        return any(self.channels[i] for i in range(6, 9))


@dataclass(slots=True)
class GfMotUVTrack:
    """One GF texture-unit UV animation block (SPICA GFMotUVTransform)."""

    name: str
    unit_index: int
    # 5 channels: SX SY Rot TX TY; empty list = bind pose.
    channels: list[list[GfMotKey]] = field(default_factory=lambda: [[] for _ in range(5)])

    @property
    def has_scale(self) -> bool:
        return any(self.channels[i] for i in range(0, 2))

    @property
    def has_rotation(self) -> bool:
        return bool(self.channels[2])

    @property
    def has_translation(self) -> bool:
        return any(self.channels[i] for i in range(3, 5))


@dataclass(slots=True)
class GfMotVisibilityTrack:
    """Per-mesh on/off flags packed one bit per frame (SPICA GFVisibilityMot)."""

    name: str
    values: list[bool] = field(default_factory=list)


@dataclass(slots=True)
class GfMotion:
    name: str
    frames_count: int
    is_looping: bool
    bones: list[GfMotBoneTrack] = field(default_factory=list)
    material_tracks: list[GfMotUVTrack] = field(default_factory=list)
    visibility_tracks: list[GfMotVisibilityTrack] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.frames_count / FRAME_RATE


def is_gf_motion(data: bytes) -> bool:
    return len(data) >= 8 and struct.unpack_from("<I", data)[0] == GFMOTION_MAGIC


def _decode_keyframes(
    data: bytes, pos: int, code: int, frames_count: int
) -> tuple[list[GfMotKey], int]:
    """Decode one track's keyframe payload starting at *pos*.

    Returns ``(keys, new_pos)``. *code* is the 3-bit track descriptor.
    """
    keys: list[GfMotKey] = []
    if code == 3:  # constant
        value = struct.unpack_from("<f", data, pos)[0]
        return [GfMotKey(0.0, value, 0.0)], pos + 4
    if code not in (4, 5):
        return keys, pos

    count = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    if frames_count > 0xFF:
        frames = list(struct.unpack_from(f"<{count}H", data, pos))
        pos += 2 * count
    else:
        frames = list(data[pos : pos + count])
        pos += count
    pos = (pos + 3) & ~3  # 4-byte alignment

    if code == 5:  # float32 value + slope per key
        for i in range(count):
            value, slope = struct.unpack_from("<2f", data, pos)
            pos += 8
            keys.append(GfMotKey(float(frames[i]), value, slope))
    else:  # code 4: quantized u16 pairs with scale/offset header
        value_scale, value_offset, slope_scale, slope_offset = struct.unpack_from(
            "<4f", data, pos
        )
        pos += 16
        for i in range(count):
            raw_value, raw_slope = struct.unpack_from("<2H", data, pos)
            pos += 4
            keys.append(
                GfMotKey(
                    float(frames[i]),
                    (raw_value / 0xFFFF) * value_scale + value_offset,
                    (raw_slope / 0xFFFF) * slope_scale + slope_offset,
                )
            )
    return keys, pos


def _parse_material_section(data: bytes, address: int, frames_count: int) -> list[GfMotUVTrack]:
    """Parse GFMotion section kind 3 (GFMaterialMot)."""
    names_count, names_length = struct.unpack_from("<iI", data, address)
    if names_count < 0 or names_count > 0x400:
        raise GfMotionError(f"implausible material count {names_count}")
    pos = address + 8
    units = list(struct.unpack_from(f"<{names_count}I", data, pos))
    pos += 4 * names_count
    names_start = pos
    names: list[str] = []
    for _ in range(names_count):
        size = data[pos]
        pos += 1
        names.append(data[pos : pos + size].decode("ascii", "replace"))
        pos += size
    pos = names_start + names_length
    tracks: list[GfMotUVTrack] = []
    for mat_name, unit_count in zip(names, units):
        for _ in range(unit_count):
            unit_index, flags, _length = struct.unpack_from("<3I", data, pos)
            pos += 12
            track = GfMotUVTrack(name=mat_name, unit_index=unit_index)
            code_bits = flags
            for channel in range(5):
                keys, pos = _decode_keyframes(data, pos, code_bits & 7, frames_count)
                track.channels[channel] = keys
                code_bits >>= 3
            tracks.append(track)
    return tracks


def _parse_visibility_section(
    data: bytes, address: int, frames_count: int
) -> list[GfMotVisibilityTrack]:
    """Parse GFMotion section kind 6 (GFVisibilityMot)."""
    names_count, names_length = struct.unpack_from("<iI", data, address)
    if names_count < 0 or names_count > 0x400:
        raise GfMotionError(f"implausible visibility mesh count {names_count}")
    pos = address + 8
    names_start = pos
    names: list[str] = []
    for _ in range(names_count):
        size = data[pos]
        pos += 1
        names.append(data[pos : pos + size].decode("ascii", "replace"))
        pos += size
    pos = names_start + names_length
    sample_count = frames_count + 1
    tracks: list[GfMotVisibilityTrack] = []
    for name in names:
        bytes_needed = (sample_count + 7) // 8
        chunk = data[pos : pos + bytes_needed]
        pos += bytes_needed
        values = [
            bool(chunk[i >> 3] & (1 << (i & 7))) for i in range(sample_count)
        ]
        tracks.append(GfMotVisibilityTrack(name=name, values=values))
    return tracks


_EYE_SCLERA_MATERIAL_RE = re.compile(
    r"^(?:[a-z]?eye(?:[a-z]|\d{1,2})?)$",
    re.IGNORECASE,
)


def _is_eye_material_name(name: str) -> bool:
    return _is_sclera_material_name(name) or _is_iris_material_name(name)


def _is_sclera_material_name(name: str) -> bool:
    return name == "Mouth" or _EYE_SCLERA_MATERIAL_RE.fullmatch(name) is not None


def _is_iris_material_name(name: str) -> bool:
    return "iris" in name.casefold()


def _eye_sheet_dims(scale_x: float, scale_y: float) -> tuple[int, int]:
    """Infer expression-sheet grid from GF albedo scale (not always 2×4)."""
    cols = max(1, int(round(abs(scale_x)))) if abs(scale_x) >= 1.0 else EYE_SHEET_COLS
    if abs(scale_y - 1.0) < 1e-6:
        rows = EYE_SHEET_ROWS
    else:
        rows = max(1, int(round(abs(scale_y))))
    return cols, rows


def gf_uv_to_map_offset(
    scale_x: float,
    scale_y: float,
    tx: float,
    ty: float,
    bind_tx: float,
    bind_ty: float,
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> tuple[float, float]:
    """Map-offset delta from bind when albedo UVs are baked at bind pose."""
    _ = (scale_x, scale_y, cols, rows)
    return (tx - bind_tx, bind_ty - ty)


def frame_index_to_map_offset(
    frame: int,
    scale_x: float,
    scale_y: float,
    bind_tx: float,
    bind_ty: float,
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> tuple[float, float]:
    """Map a sheet frame index to three.js ``map.offset`` from bind-pose bake."""
    tx, ty = frame_index_to_gf_translation(
        frame, bind_tx, bind_ty, cols=cols, rows=rows
    )
    return gf_uv_to_map_offset(
        scale_x, scale_y, tx, ty, bind_tx, bind_ty, cols=cols, rows=rows
    )


def gf_frame_to_texture_offset(
    frame: int,
    scale_x: float,
    bind_tx: float,
    bind_ty: float,
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> tuple[float, float]:
    """three.js ``map.offset`` for raw-U eyes with mirror wrap.

  ``offset.x`` selects the sheet column in tile space (``col * scale/cols``) so
  both eyes sample the same expression and mirror— not adjacent columns.
    """
    col = frame % cols
    _tx, ty = frame_index_to_gf_translation(
        frame, bind_tx, bind_ty, cols=cols, rows=rows
    )
    ox = col * abs(scale_x) / cols
    oy = ty - bind_ty
    return ox, oy


def gf_translation_to_texture_offset(
    tx: float,
    ty: float,
    bind_tx: float,
    bind_ty: float,
    *,
    scale_x: float = 2.0,
    cols: int = EYE_SHEET_COLS,
) -> tuple[float, float]:
    """Legacy helper; prefer ``gf_frame_to_texture_offset`` per frame index."""
    col = _gf_tx_to_sheet_col(tx, bind_tx, cols)
    ox = col * abs(scale_x) / cols
    oy = ty - bind_ty
    return ox, oy


def _gf_tx_to_sheet_col(tx: float, bind_tx: float, cols: int) -> int:
    """Map GF albedo TX to 0-based sheet column for 2-wide eye sheets."""
    if cols <= 1:
        return 0
    if abs(bind_tx - 1.0) < 0.01:
        return 0 if abs(tx - 1.0) < 0.01 else 1
    if abs(bind_tx - 0.5) < 0.01:
        return 0 if abs(tx - 0.5) < 0.01 else 1
    step = 0.5
    return max(0, min(cols - 1, int(round((bind_tx - tx) / step))))


def eye_expression_frame_translations(
    scale_x: float,
    scale_y: float,
    bind_tx: float,
    bind_ty: float,
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> list[list[float]]:
    """GF ``TX``/``TY`` per expression frame for GLB ``eyeExpression``."""
    _ = (scale_x, scale_y)
    return [
        list(
            frame_index_to_gf_translation(
                frame, bind_tx, bind_ty, cols=cols, rows=rows
            )
        )
        for frame in range(cols * rows)
    ]


def eye_expression_frame_offsets(
    scale_x: float,
    scale_y: float,
    bind_tx: float,
    bind_ty: float,
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> list[list[float]]:
    """Precompute per-frame ``map.offset`` for raw-U eye meshes (repeat = sheet scale)."""
    return [
        list(
            gf_frame_to_texture_offset(
                frame, scale_x, bind_tx, bind_ty, cols=cols, rows=rows
            )
        )
        for frame in range(cols * rows)
    ]


def frame_index_to_gf_translation(
    frame: int,
    bind_tx: float,
    bind_ty: float,
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> tuple[float, float]:
    """Map a 2×4 sheet frame index to GF translation values."""
    col = frame % cols
    row = frame // cols
    # Ultra Moon Eye bind uses TX=1 for column 0; the mirrored column is TX=0.5.
    if abs(bind_tx - 1.0) < 0.01:
        tx = 1.0 if col == 0 else 0.5
    elif abs(bind_tx - 0.5) < 0.01:
        tx = 0.5 if col == 0 else 1.0
    else:
        step = 0.5
        tx = bind_tx - col * step
    ty = bind_ty + row / rows
    return tx, ty


def parse_gf_motion(data: bytes, name: str = "motion") -> GfMotion:
    if not is_gf_motion(data):
        raise GfMotionError("not a GFMotion (bad magic)")
    section_count = struct.unpack_from("<I", data, 4)[0]
    if not 1 <= section_count <= 16:
        raise GfMotionError(f"implausible section count {section_count}")
    sections: list[tuple[int, int, int]] = []
    pos = 8
    for _ in range(section_count):
        kind, length, address = struct.unpack_from("<3I", data, pos)
        pos += 12
        sections.append((kind, length, address))
    if sections[0][0] != _SECT_SUBHEADER:
        raise GfMotionError("first section is not the sub header")

    sub = sections[0][2]
    frames_count, loop_flags = struct.unpack_from("<IH", data, sub)
    motion = GfMotion(name=name, frames_count=frames_count, is_looping=bool(loop_flags & 1))
    if frames_count > 0x10000:
        raise GfMotionError(f"implausible frame count {frames_count}")

    for kind, _length, address in sections[1:]:
        if kind == _SECT_SKELETAL:
            names_count, names_length = struct.unpack_from("<iI", data, address)
            if names_count < 0 or names_count > 0x400:
                raise GfMotionError(f"implausible bone count {names_count}")
            pos = address + 8
            names: list[str] = []
            blob_end = pos + names_length
            for _ in range(names_count):
                size = data[pos]
                pos += 1
                names.append(data[pos : pos + size].decode("ascii", "replace"))
                pos += size
            pos = blob_end
            for bone_name in names:
                flags, _length = struct.unpack_from("<2I", data, pos)
                pos += 8
                track = GfMotBoneTrack(name=bone_name, is_axis_angle=(flags >> 31) == 0)
                code_bits = flags
                for channel in range(9):
                    keys, pos = _decode_keyframes(data, pos, code_bits & 7, frames_count)
                    track.channels[channel] = keys
                    code_bits >>= 3
                motion.bones.append(track)
        elif kind in (_SECT_MATERIAL, _SECT_MATERIAL_ALT):
            motion.material_tracks.extend(_parse_material_section(data, address, frames_count))
        elif kind == _SECT_VISIBILITY:
            motion.visibility_tracks.extend(
                _parse_visibility_section(data, address, frames_count)
            )
    return motion


def mesh_bind_visibility(
    mesh_names: Iterable[str],
    motions: Iterable[GfMotion],
    *,
    opt_mesh_materials: dict[str, list[str]] | None = None,
    opt_mesh_geometry: dict[
        str, tuple[tuple[tuple[int, int], ...], tuple[float, float, float, float, float, float]]
    ]
    | None = None,
) -> dict[str, bool]:
    """Resolve bind visibility from source tracks and geometry-matched alternatives."""
    names = list(mesh_names)
    defaults = {name: True for name in names}
    motion_list = list(motions)
    ref = next((m for m in motion_list if m.name.endswith("_00")), None)
    if ref is None:
        ref = next((m for m in motion_list if m.visibility_tracks), None)
    tracked: set[str] = set()
    if ref is not None:
        for track in ref.visibility_tracks:
            if track.name in defaults and track.values:
                defaults[track.name] = track.values[0]
                tracked.add(track.name)
    materials = opt_mesh_materials or {}
    geometry = opt_mesh_geometry or {}

    def material_variant_key(material_names: list[str]) -> str:
        joined = "|".join(material_names).casefold()
        return re.sub(r"(?:vco|none|[^a-z0-9])", "", joined)

    def geometry_matches(left_name: str, right_name: str) -> bool:
        left = geometry.get(left_name)
        right = geometry.get(right_name)
        if left is None or right is None or left[0] != right[0]:
            return False
        left_bounds, right_bounds = left[1], right[1]
        extent = max(
            left_bounds[3] - left_bounds[0],
            left_bounds[4] - left_bounds[1],
            left_bounds[5] - left_bounds[2],
            1.0,
        )
        tolerance = extent * 0.005
        return all(abs(a - b) <= tolerance for a, b in zip(left_bounds, right_bounds))

    for mesh_name in names:
        if mesh_name in tracked:
            continue
        if not mesh_name.endswith("_OptMesh"):
            continue
        mat_names = materials.get(mesh_name, [])
        if not any("vco" in material.casefold() for material in mat_names):
            continue
        variant_key = material_variant_key(mat_names)
        for other_name in names:
            other_materials = materials.get(other_name, [])
            if (
                other_name == mesh_name
                or other_name in tracked
                or any("vco" in material.casefold() for material in other_materials)
                or material_variant_key(other_materials) != variant_key
            ):
                continue
            if geometry_matches(mesh_name, other_name):
                defaults[mesh_name] = False
                break
    return defaults


def visibility_track_export(track: GfMotVisibilityTrack) -> list[bool]:
    return list(track.values)


# -- evaluation ---------------------------------------------------------------


def _herp(v0: float, v1: float, s0: float, s1: float, diff: float, weight: float) -> float:
    """Hermite interpolation matching SPICA's Interpolation.Herp."""
    result = v0 + (v0 - v1) * (2 * weight - 3) * weight * weight
    result += (diff * (weight - 1)) * (s0 * (weight - 1) + s1 * weight)
    return result


def sample_track(keys: list[GfMotKey], frame: float, default: float) -> float:
    if not keys:
        return default
    if len(keys) == 1:
        return keys[0].value
    if frame <= keys[0].frame:
        return keys[0].value
    if frame >= keys[-1].frame:
        return keys[-1].value
    lhs = keys[0]
    for key in keys:
        if key.frame <= frame:
            lhs = key
        if key.frame >= frame:
            rhs = key
            break
    else:  # pragma: no cover - guarded by the range checks above
        return keys[-1].value
    if rhs.frame == lhs.frame:
        return lhs.value
    diff = frame - lhs.frame
    weight = diff / (rhs.frame - lhs.frame)
    return _herp(lhs.value, rhs.value, lhs.slope, rhs.slope, diff, weight)


def _quat_from_euler_xyz(x: float, y: float, z: float) -> tuple[float, float, float, float]:
    """q = qz * qy * qx (SPICA H3D convention), returned as (x, y, z, w)."""
    cx, sx = math.cos(x * 0.5), math.sin(x * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    cz, sz = math.cos(z * 0.5), math.sin(z * 0.5)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def _quat_from_axis_angle_vector(
    x: float, y: float, z: float
) -> tuple[float, float, float, float]:
    """GFMotion axis-angle rotation: vector direction = axis, |v| * 2 = angle."""
    length = math.sqrt(x * x + y * y + z * z)
    angle = length * 2.0
    if angle <= 1e-9:
        return (0.0, 0.0, 0.0, 1.0)
    s = math.sin(angle * 0.5) / length
    return (x * s, y * s, z * s, math.cos(angle * 0.5))


@dataclass(slots=True)
class BakedBoneAnim:
    name: str
    # Parallel to the frame-time list produced by bake_motion.
    translations: list[tuple[float, float, float]] | None = None
    rotations: list[tuple[float, float, float, float]] | None = None
    scales: list[tuple[float, float, float]] | None = None


def bake_motion(
    motion: GfMotion,
    rest_pose: dict[str, tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]],
) -> tuple[list[float], list[BakedBoneAnim]]:
    """Sample every animated bone at each frame.

    *rest_pose* maps bone name -> (scale, rotation_euler_xyz, translation)
    from the GFModel skeleton; unanimated channels fall back to it.
    Returns ``(times_seconds, baked_bones)``.
    """
    frame_indices = list(range(motion.frames_count + 1))
    times = [f / FRAME_RATE for f in frame_indices]
    baked: list[BakedBoneAnim] = []
    for track in motion.bones:
        rest = rest_pose.get(track.name)
        if rest is None:
            continue
        rest_scale, rest_rot, rest_trans = rest
        anim = BakedBoneAnim(name=track.name)
        if track.has_translation:
            anim.translations = []
        if track.has_scale:
            anim.scales = []
        if track.has_rotation:
            anim.rotations = []
        if anim.translations is None and anim.scales is None and anim.rotations is None:
            continue
        prev_q: tuple[float, float, float, float] | None = None
        for frame in frame_indices:
            if anim.scales is not None:
                anim.scales.append(
                    (
                        sample_track(track.channels[0], frame, rest_scale[0]),
                        sample_track(track.channels[1], frame, rest_scale[1]),
                        sample_track(track.channels[2], frame, rest_scale[2]),
                    )
                )
            if anim.rotations is not None:
                # SPICA keeps the bind-pose Euler component whenever a channel
                # has no keys, even for axis-angle tracks.
                rx = sample_track(track.channels[3], frame, rest_rot[0])
                ry = sample_track(track.channels[4], frame, rest_rot[1])
                rz = sample_track(track.channels[5], frame, rest_rot[2])
                if track.is_axis_angle:
                    q = _quat_from_axis_angle_vector(rx, ry, rz)
                else:
                    q = _quat_from_euler_xyz(rx, ry, rz)
                if prev_q is not None:
                    dot = sum(a * b for a, b in zip(q, prev_q))
                    if dot < 0.0:
                        q = (-q[0], -q[1], -q[2], -q[3])
                prev_q = q
                anim.rotations.append(q)
            if anim.translations is not None:
                anim.translations.append(
                    (
                        sample_track(track.channels[6], frame, rest_trans[0]),
                        sample_track(track.channels[7], frame, rest_trans[1]),
                        sample_track(track.channels[8], frame, rest_trans[2]),
                    )
                )
        baked.append(anim)
    return times, baked


@dataclass(slots=True)
class BakedMaterialOffset:
    material: str
    translations: list[tuple[float, float]]


def _motion_uv_track(
    motion: GfMotion, material_name: str, unit_index: int = 0
) -> GfMotUVTrack | None:
    for track in motion.material_tracks:
        if track.name == material_name and track.unit_index == unit_index:
            return track
    return None


def bake_material_motion(
    motion: GfMotion,
    rest_uv: dict[str, tuple[float, float, float, float]],
    *,
    cols: int = EYE_SHEET_COLS,
    rows: int = EYE_SHEET_ROWS,
) -> list[BakedMaterialOffset]:
    """Sample Eye sclera GF UV translation per frame (albedo unit 0).

    Iris materials are single-frame; they are included only when unit 0 has
    its own keys (rare). Eye expression frames are not propagated to iris.
    """
    frame_indices = list(range(motion.frames_count + 1))
    if "Eye" not in rest_uv:
        return []

    driver = _motion_uv_track(motion, "Eye", 0)
    if driver is None or not (driver.has_translation or driver.has_scale):
        driver = next(
            (
                t
                for t in motion.material_tracks
                if t.unit_index == 0
                and _is_sclera_material_name(t.name)
                and (t.has_translation or t.has_scale)
            ),
            None,
        )

    targets: list[str] = ["Eye"]
    for mat_name in rest_uv:
        if not _is_iris_material_name(mat_name):
            continue
        own = _motion_uv_track(motion, mat_name, 0)
        if own is not None and (own.has_translation or own.has_scale):
            targets.append(mat_name)

    baked: list[BakedMaterialOffset] = []
    for mat_name in targets:
        bind_tx, bind_ty = rest_uv[mat_name][2], rest_uv[mat_name][3]
        own = _motion_uv_track(motion, mat_name, 0)
        if mat_name == "Eye":
            track = own if own is not None and (own.has_translation or own.has_scale) else driver
        else:
            track = own
        anim = BakedMaterialOffset(material=mat_name, translations=[])
        for frame in frame_indices:
            if track is not None:
                tx = sample_track(track.channels[3], frame, bind_tx)
                ty = sample_track(track.channels[4], frame, bind_ty)
            else:
                tx, ty = bind_tx, bind_ty
            anim.translations.append((tx, ty))
        baked.append(anim)
    return baked


# -- world / battle map material motion ----------------------------------------


def _material_unit_uv(
    model: Any, material_name: str, unit_index: int
) -> tuple[float, float, float, float] | None:
    """GF albedo scale/translation for one material texture unit."""
    from .gf import GfModel

    if not isinstance(model, GfModel):
        return None
    for mat in model.materials:
        if mat.name != material_name:
            continue
        for unit in mat.texture_units:
            if unit.unit_index == unit_index:
                return (
                    unit.scale[0],
                    unit.scale[1],
                    unit.translation[0],
                    unit.translation[1],
                )
    return None


def _world_motion_track_signature(motion: GfMotion) -> tuple[Any, ...]:
    mat_bits = tuple(
        sorted(
            (
                track.name,
                track.unit_index,
                bool(track.has_translation),
                bool(track.has_scale),
            )
            for track in motion.material_tracks
            if track.has_translation or track.has_scale
        )
    )
    vis_bits = tuple(sorted(track.name for track in motion.visibility_tracks))
    return (motion.frames_count, motion.is_looping, mat_bits, vis_bits)


def dedupe_world_motions(motions: Iterable[GfMotion]) -> list[GfMotion]:
    """Drop duplicate embedded battle-map clips (payloads repeat weather sets)."""
    out: list[GfMotion] = []
    seen: set[tuple[Any, ...]] = set()
    for motion in motions:
        sig = _world_motion_track_signature(motion)
        if sig in seen:
            continue
        seen.add(sig)
        out.append(motion)
    return out


def world_motion_effective_loop(motion: GfMotion) -> bool:
    """GF battle backgrounds often loop long ambient UV cycles even when the flag is clear."""
    if motion.is_looping:
        return True
    if motion.frames_count < 300:
        return False
    return any(track.has_translation or track.has_scale for track in motion.material_tracks)


# GF battle-map UV deltas at or below this are exported as plant wind, not scroll.
WIND_UV_AMP_MAX = 0.12


def _is_scroll_material(material_name: str) -> bool:
    """Materials that intentionally use large UV translation (water, fire, streaks)."""
    low = material_name.casefold()
    return any(
        token in low
        for token in (
            "unami",
            "fire01",
            "light02",
            "sea_iro",
            "wind01",
            "kaze",
            "_wind",
            "_kaze",
        )
    )


def _is_grass_wind_material(material_name: str) -> bool:
    """Grass, flowers, and garden plants that use small dual-unit TEV wind."""
    low = material_name.casefold()
    if any(
        token in low
        for token in ("jime", "iwa", "mori", "hana", "_ji1", "_ji2", "kusa_ji")
    ):
        return False
    if any(token in low for token in ("kusa_kusa", "kusa_ueki", "siba_kusa")):
        return True
    if "kusa01" in low:
        return True
    if "ueki" in low:
        return True
    return False


def _material_accepts_world_uv_motion(material_name: str) -> bool:
    """Whether a material may be considered for map UV motion export."""
    low = material_name.casefold()
    if any(token in low for token in ("jime", "stage", "rain", "snow")):
        return False
    # Dual-unit sea tint uses per-vertex alpha; scrolling one exported layer looks wrong.
    if "sea_iro" in low:
        return False
    if _is_scroll_material(material_name) or _is_grass_wind_material(material_name):
        return True
    if any(token in low for token in ("kusa", "ueki", "hana")):
        return False
    return True


def _classify_uv_motion_kind(material_name: str, max_delta: float) -> str | None:
    """Classify baked offset magnitude as wind, scroll, or skip."""
    if max_delta < 1e-6:
        return None
    if _is_scroll_material(material_name):
        return "scroll"
    if max_delta <= WIND_UV_AMP_MAX:
        return "wind"
    if _is_grass_wind_material(material_name):
        return None
    return "scroll"


def _world_motion_clip_id(motion: GfMotion, index: int) -> str:
    uv_count = sum(
        1
        for track in motion.material_tracks
        if track.has_translation or track.has_scale
    )
    vis_count = len(motion.visibility_tracks)
    if world_motion_effective_loop(motion) and uv_count:
        kind = "ambient"
    elif vis_count > 2:
        kind = "weather"
    elif uv_count:
        kind = "uv"
    else:
        kind = "vis"
    loop_tag = "loop" if world_motion_effective_loop(motion) else "once"
    return f"{kind}_{motion.frames_count}f_{loop_tag}_{index:02d}"


def pick_default_world_motion_clip(clips: list[dict]) -> str | None:
    """Prefer the longest primary ambient UV clip for autoplay (one clip, not a merge)."""
    if not clips:
        return None

    def material_priority(clip: dict) -> int:
        names = " ".join(str(track.get("material") or "") for track in clip.get("tracks") or [])
        low = names.casefold()
        if any(token in low for token in ("unami", "fire01", "light02", "kusa_kusa", "kusa_ueki", "siba_kusa")):
            return 3
        if any(token in low for token in ("kusa", "jime", "stage")):
            return 0
        if "iro" in low or "sea_" in low:
            return 1
        return 2

    def score(clip: dict) -> tuple[int, int, int, int, int, int]:
        tracks = clip.get("tracks") or []
        if not tracks:
            return (-1, -1, -1, -1, -1, -1)
        frame_count = int(clip.get("frameCount") or 0)
        is_loop = 1 if clip.get("loop") else 0
        clip_id = str(clip.get("id", ""))
        is_weather = 1 if clip_id.startswith("weather_") else 0
        is_short_pulse = 1 if frame_count <= 60 else 0
        is_primary = 1 if frame_count >= 300 else 0
        return (
            is_loop,
            is_primary,
            material_priority(clip),
            len(tracks),
            frame_count,
            -is_weather,
            -is_short_pulse,
        )

    with_tracks = [clip for clip in clips if clip.get("tracks")]
    if not with_tracks:
        return str(clips[0]["id"]) if clips else None
    ambient = [
        clip
        for clip in with_tracks
        if str(clip.get("id", "")).startswith("ambient_")
        and int(clip.get("frameCount") or 0) >= 60
    ]
    pool = ambient or with_tracks
    best = max(pool, key=score)
    return str(best["id"])


def pick_ambient_overlay_clips(clips: list[dict], default_id: str | None) -> list[str]:
    """Long ambient loops that layer on the default clip (e.g. slow sea caustics)."""
    if not default_id:
        return []
    default_mats = {
        str(track.get("material") or "")
        for clip in clips
        if str(clip.get("id")) == default_id
        for track in clip.get("tracks") or []
    }
    overlays: list[str] = []
    for clip in clips:
        clip_id = str(clip.get("id") or "")
        if clip_id == default_id or not clip.get("loop"):
            continue
        if not clip_id.startswith("ambient_"):
            continue
        if int(clip.get("frameCount") or 0) < 300:
            continue
        tracks = clip.get("tracks") or []
        if not tracks:
            continue
        if any(str(track.get("material") or "") in default_mats for track in tracks):
            continue
        if any("sea_iro" in str(track.get("material") or "").casefold() for track in tracks):
            continue
        overlays.append(clip_id)
    return overlays


def _should_export_world_uv_track(
    track: GfMotUVTrack, motion_tracks: list[GfMotUVTrack]
) -> bool:
    """Map motion targets the albedo unit exported to GLB (unit 0)."""
    if not (track.has_translation or track.has_scale):
        return False
    if track.unit_index == 0:
        return True
    same_mat = [t for t in motion_tracks if t.name == track.name]
    if any(
        t.unit_index == 0 and (t.has_translation or t.has_scale) for t in same_mat
    ):
        return False
    # Grass/plant wind often keys unit 1 while export uses unit-0 albedo.
    if track.unit_index == 1 and _is_grass_wind_material(track.name):
        return True
    return False


def bake_world_map_motion_clip(
    motion: GfMotion,
    model: Any,
    *,
    material_names: set[str],
) -> list[dict]:
    """Bake GF UV motion into per-frame ``map.offset`` deltas for one clip."""
    frame_indices = list(range(motion.frames_count + 1))
    baked: list[dict] = []
    for track in motion.material_tracks:
        if track.name not in material_names:
            continue
        if not _material_accepts_world_uv_motion(track.name):
            continue
        if not _should_export_world_uv_track(track, motion.material_tracks):
            continue
        bind = _material_unit_uv(model, track.name, 0)
        if bind is None:
            continue
        bind_sx, bind_sy, bind_tx, bind_ty = bind
        sample_bind = _material_unit_uv(model, track.name, track.unit_index) or bind
        offsets: list[list[float]] = []
        for frame in frame_indices:
            tx = sample_track(track.channels[3], frame, sample_bind[2])
            ty = sample_track(track.channels[4], frame, sample_bind[3])
            ox, oy = gf_uv_to_map_offset(
                bind_sx,
                bind_sy,
                tx,
                ty,
                bind_tx,
                bind_ty,
            )
            offsets.append([ox, oy])
        if all(abs(ox) < 1e-6 and abs(oy) < 1e-6 for ox, oy in offsets):
            continue
        max_delta = max(max(abs(ox) for ox, _ in offsets), max(abs(oy) for _, oy in offsets))
        motion_kind = _classify_uv_motion_kind(track.name, max_delta)
        if motion_kind is None:
            continue
        baked.append(
            {
                "material": track.name,
                "frameOffsets": offsets,
                "motionKind": motion_kind,
            }
        )
    return baked


def build_world_map_material_motion(
    motions: Iterable[GfMotion],
    model: Any,
    *,
    material_names: Iterable[str],
) -> dict | None:
    """Build root ``extras.rae.mapMaterialMotion`` for battle / world maps."""
    names = set(material_names)
    clips: list[dict] = []
    for index, motion in enumerate(dedupe_world_motions(motions)):
        tracks = bake_world_map_motion_clip(motion, model, material_names=names)
        vis = {
            track.name: visibility_track_export(track)
            for track in motion.visibility_tracks
        }
        if not tracks and not vis:
            continue
        clip: dict = {
            "id": _world_motion_clip_id(motion, index),
            "frameCount": motion.frames_count + 1,
            "loop": world_motion_effective_loop(motion),
            "tracks": tracks,
        }
        if vis:
            clip["meshVisibility"] = vis
        clips.append(clip)
    if not clips:
        return None
    default_clip = pick_default_world_motion_clip(clips)
    overlay_clips = pick_ambient_overlay_clips(clips, default_clip)
    payload: dict = {
        "frameRate": FRAME_RATE,
        "defaultClip": default_clip,
        "clips": clips,
    }
    if overlay_clips:
        payload["overlayClips"] = overlay_clips
    return payload


def world_visibility_gltf_animations(motions: Iterable[GfMotion]) -> list[dict]:
    """Visibility-only glTF animation stubs (no skeletal channels)."""
    animations: list[dict] = []
    for index, motion in enumerate(dedupe_world_motions(motions)):
        if not motion.visibility_tracks:
            continue
        animations.append(
            {
                "name": _world_motion_clip_id(motion, index),
                "samplers": [],
                "channels": [],
                "extras": {
                    "rae": {
                        "meshVisibility": {
                            track.name: visibility_track_export(track)
                            for track in motion.visibility_tracks
                        }
                    }
                },
            }
        )
    return animations
