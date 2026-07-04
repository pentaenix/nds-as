"""GFMotion material UV section (kind 3) parsing and baking."""
from __future__ import annotations

import math
import struct

import pytest

from rae.platforms.threeds.motion import (
    GFMOTION_MAGIC,
    bake_material_motion,
    eye_expression_frame_offsets,
    gf_frame_to_texture_offset,
    gf_translation_to_texture_offset,
    gf_uv_to_map_offset,
    parse_gf_motion,
)


def _quantized_track(frames: list[int], values: list[float], slopes: list[float]) -> bytes:
    vmin, vmax = min(values), max(values)
    smin, smax = min(slopes), max(slopes)
    vscale = (vmax - vmin) or 1.0
    sscale = (smax - smin) or 1.0
    blob = struct.pack("<I", len(frames))
    blob += bytes(frames)
    while len(blob) % 4:
        blob += b"\x00"
    blob += struct.pack("<4f", vscale, vmin, sscale, smin)
    for value, slope in zip(values, slopes):
        qv = round((value - vmin) / vscale * 0xFFFF)
        qs = round((slope - smin) / sscale * 0xFFFF)
        blob += struct.pack("<2H", qv, qs)
    return blob


def _material_section(frames_count: int) -> bytes:
    """One Eye material, one unit, animated TY (channel 4 = code 4)."""
    # flags: ch0-3 = 0, ch4 TY = quantized (4 << 12) = 0x4000
    flags = 4 << 12
    ty = _quantized_track([0, frames_count], [0.0, 2.0], [0.0, 0.0])
    unit_block = struct.pack("<3I", 0, flags, 0) + ty
    names = b"\x03Eye"
    header = struct.pack("<iI", 1, 4) + struct.pack("<I", 1) + names
    return header + unit_block


def _motion_with_material(frames_count: int = 10) -> bytes:
    sub_header = struct.pack("<IHH", frames_count, 1, 0)
    sub_header += struct.pack("<3f", 0, 0, 0) + struct.pack("<3f", 1, 1, 1)
    sub_header += struct.pack("<I", 0xBEEF)
    material = _material_section(frames_count)

    header_len = 8 + 3 * 12
    sub_addr = header_len
    skel_addr = sub_addr + len(sub_header)
    mat_addr = skel_addr  # empty skeletal: names_count=0
    # skeletal with zero bones
    skeletal = struct.pack("<iI", 0, 0)
    mat_addr = skel_addr + len(skeletal)
    vis_addr = mat_addr + len(material)

    out = struct.pack("<2I", GFMOTION_MAGIC, 3)
    out += struct.pack("<3I", 0, len(sub_header), sub_addr)
    out += struct.pack("<3I", 1, len(skeletal), skel_addr)
    out += struct.pack("<3I", 3, len(material), mat_addr)
    return out + sub_header + skeletal + material


def test_gf_translation_to_texture_offset_bind_frame():
    # Column 0 (frames 0,2,…): ox=0; column 1 (frames 1,3,…): ox=1 with scale 2.
    off = gf_frame_to_texture_offset(0, 2.0, 1.0, 0.0)
    assert off == pytest.approx((0.0, 0.0))
    off1 = gf_frame_to_texture_offset(1, 2.0, 1.0, 0.0)
    assert off1 == pytest.approx((1.0, 0.0))


def test_eye_expression_frame_offsets_match_sheet():
    offsets = eye_expression_frame_offsets(2.0, 1.0, 1.0, 0.0)
    assert offsets[0] == pytest.approx([0.0, 0.0])
    assert offsets[1] == pytest.approx([1.0, 0.0])
    assert offsets[2] == pytest.approx([0.0, 0.25])
    assert offsets[7] == pytest.approx([1.0, 0.75])


def test_parse_material_section_and_bake():
    data = _motion_with_material(10)
    motion = parse_gf_motion(data, "eye_blink")
    assert motion.frames_count == 10
    assert len(motion.material_tracks) == 1
    track = motion.material_tracks[0]
    assert track.name == "Eye"
    assert track.unit_index == 0
    assert track.has_translation

    rest = {"Eye": (2.0, 1.0, 1.0, 0.0)}
    baked = bake_material_motion(motion, rest)
    assert len(baked) == 1
    eye = baked[0]
    assert eye.material == "Eye"
    assert len(eye.translations) == 11
    assert eye.translations[0] == pytest.approx((1.0, 0.0))
    assert eye.translations[-1] == pytest.approx((1.0, 2.0))


def test_bake_material_motion_bind_when_unit0_unkeyed():
    data = _motion_with_material(5)
    motion = parse_gf_motion(data, "blink")
    # Drop Eye track so only bind pose remains for albedo unit 0.
    motion.material_tracks.clear()
    rest = {"Eye": (2.0, 1.0, 1.0, 0.0)}
    baked = bake_material_motion(motion, rest)
    assert len(baked) == 1
    assert baked[0].translations == [(1.0, 0.0)] * 6


def test_bake_material_motion_eye_only_not_iris_driver():
    data = _motion_with_material(5)
    motion = parse_gf_motion(data, "blink")
    rest = {
        "Eye": (2.0, 1.0, 1.0, 0.0),
        "LIris": (-4.0, 4.0, 1.0, 0.0),
        "RIris": (4.0, 4.0, 1.0, 0.0),
    }
    baked = bake_material_motion(motion, rest)
    names = {b.material for b in baked}
    assert names == {"Eye"}
    eye = next(b for b in baked if b.material == "Eye")
    assert eye.translations[-1] == pytest.approx((1.0, 2.0))
