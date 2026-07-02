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
import struct
from dataclasses import dataclass, field

GFMOTION_MAGIC = 0x00060000

_SECT_SUBHEADER = 0
_SECT_SKELETAL = 1

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
class GfMotion:
    name: str
    frames_count: int
    is_looping: bool
    bones: list[GfMotBoneTrack] = field(default_factory=list)

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
        if kind != _SECT_SKELETAL:
            continue  # material / visibility animations are not exported
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
    return motion


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
