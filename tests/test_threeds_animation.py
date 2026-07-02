"""3DS GFMotion parsing + skinned/animated GLB export tests."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

import pytest

from rae.platforms.threeds.motion import (
    GFMOTION_MAGIC,
    GfMotKey,
    _decode_keyframes,
    bake_motion,
    is_gf_motion,
    parse_gf_motion,
    sample_track,
)

ROM = Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"


# -- synthetic key decoding ----------------------------------------------------


def _quantized_track(frames: list[int], values: list[float], slopes: list[float]) -> bytes:
    """Encode a code-4 (quantized u16) track the way the ROM stores it."""
    vmin, vmax = min(values), max(values)
    smin, smax = min(slopes), max(slopes)
    vscale = (vmax - vmin) or 1.0
    sscale = (smax - smin) or 1.0
    blob = struct.pack("<I", len(frames))
    blob += bytes(frames)  # frame count < 0x100 -> u8 frames
    while len(blob) % 4:
        blob += b"\x00"
    blob += struct.pack("<4f", vscale, vmin, sscale, smin)
    for value, slope in zip(values, slopes):
        qv = round((value - vmin) / vscale * 0xFFFF)
        qs = round((slope - smin) / sscale * 0xFFFF)
        blob += struct.pack("<2H", qv, qs)
    return blob


def test_decode_constant_track():
    data = struct.pack("<f", 2.5)
    keys, pos = _decode_keyframes(data, 0, 3, 10)
    assert pos == 4
    assert len(keys) == 1
    assert keys[0].value == pytest.approx(2.5)


def test_decode_float_keyframes_with_u16_frames():
    # frames_count > 0xFF forces u16 frame indices.
    blob = struct.pack("<I", 2) + struct.pack("<2H", 0, 300)
    blob += struct.pack("<2f", 1.0, 0.5) + struct.pack("<2f", -1.0, 0.25)
    keys, pos = _decode_keyframes(blob, 0, 5, 300)
    assert pos == len(blob)
    assert [k.frame for k in keys] == [0, 300]
    assert keys[0].value == pytest.approx(1.0)
    assert keys[1].slope == pytest.approx(0.25)


def test_decode_quantized_keyframes_roundtrip():
    frames = [0, 10, 25]
    values = [0.0, 1.5, -0.75]
    slopes = [0.1, -0.2, 0.0]
    blob = _quantized_track(frames, values, slopes)
    keys, pos = _decode_keyframes(blob, 0, 4, 25)
    assert pos == len(blob)
    assert [k.frame for k in keys] == frames
    for key, value, slope in zip(keys, values, slopes):
        assert key.value == pytest.approx(value, abs=1e-3)
        assert key.slope == pytest.approx(slope, abs=1e-3)


def test_decode_quantized_frame_alignment_padding():
    # 3 u8 frames -> 1 byte of alignment padding before the scale header.
    blob = _quantized_track([0, 1, 2], [1.0, 2.0, 3.0], [0.0, 0.0, 0.0])
    assert (4 + 3 + 1 + 16 + 3 * 4) == len(blob)
    keys, _pos = _decode_keyframes(blob, 0, 4, 2)
    assert keys[2].value == pytest.approx(3.0, abs=1e-3)


def test_sample_track_hermite_and_clamping():
    keys = [GfMotKey(0, 0.0, 0.0), GfMotKey(10, 1.0, 0.0)]
    assert sample_track(keys, -5, 9.9) == pytest.approx(0.0)
    assert sample_track(keys, 15, 9.9) == pytest.approx(1.0)
    mid = sample_track(keys, 5, 9.9)
    assert mid == pytest.approx(0.5, abs=1e-6)  # zero slopes -> smoothstep midpoint
    assert sample_track([], 3, 7.25) == pytest.approx(7.25)  # empty -> rest value


def _synthetic_motion(frames_count: int = 20) -> bytes:
    """One-bone GFMotion: constant TX plus a quantized RZ (Euler flag set)."""
    name = b"\x05Waist"
    bone_names = name + b"\x00" * ((4 - len(name) % 4) % 4)
    # track codes: RZ (channel 5) = 4 (quantized), TX (channel 6) = 3 (constant)
    flags = (1 << 31) | (4 << (5 * 3)) | (3 << (6 * 3))
    rz = _quantized_track([0, frames_count], [0.0, math.pi / 2], [0.0, 0.0])
    tx = struct.pack("<f", 4.25)
    bone_block = struct.pack("<2I", flags, 0) + rz + tx
    skeletal = struct.pack("<iI", 1, len(bone_names)) + bone_names + bone_block

    sub_header = struct.pack("<IHH", frames_count, 1, 0)
    sub_header += struct.pack("<3f", 0, 0, 0) + struct.pack("<3f", 1, 1, 1)
    sub_header += struct.pack("<I", 0xDEAD)

    header_len = 8 + 2 * 12
    sub_addr = header_len
    skel_addr = sub_addr + len(sub_header)
    out = struct.pack("<2I", GFMOTION_MAGIC, 2)
    out += struct.pack("<3I", 0, len(sub_header), sub_addr)
    out += struct.pack("<3I", 1, len(skeletal), skel_addr)
    return out + sub_header + skeletal


def test_parse_synthetic_motion_and_bake():
    data = _synthetic_motion()
    assert is_gf_motion(data)
    motion = parse_gf_motion(data, "synthetic")
    assert motion.frames_count == 20
    assert motion.is_looping
    assert len(motion.bones) == 1
    track = motion.bones[0]
    assert track.name == "Waist"
    assert not track.is_axis_angle  # bit 31 set -> Euler
    assert track.has_rotation and track.has_translation and not track.has_scale

    rest = {"Waist": ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0), (0.0, 9.0, 0.0))}
    times, baked = bake_motion(motion, rest)
    assert len(times) == 21
    assert times[-1] == pytest.approx(20 / 30)
    (anim,) = baked
    assert anim.scales is None
    # Constant TX overrides rest X; unanimated TY/TZ keep the rest pose.
    assert anim.translations[0] == pytest.approx((4.25, 9.0, 0.0))
    # Euler Z rotation 0 -> identity, pi/2 -> 90 degrees about Z; unit quats.
    assert anim.rotations[0] == pytest.approx((0.0, 0.0, 0.0, 1.0), abs=1e-3)
    end = anim.rotations[-1]
    assert end[2] == pytest.approx(math.sin(math.pi / 4), abs=1e-3)
    assert end[3] == pytest.approx(math.cos(math.pi / 4), abs=1e-3)
    for q in anim.rotations:
        assert sum(c * c for c in q) == pytest.approx(1.0, abs=1e-4)


def test_parse_rejects_garbage():
    with pytest.raises(ValueError):
        parse_gf_motion(b"\x00" * 64)
    assert not is_gf_motion(b"PC\x00\x00")


# -- integration against the real ROM ------------------------------------------


def _read_glb_json(path: Path) -> dict:
    raw = path.read_bytes()
    assert raw[:4] == b"glTF"
    json_len = struct.unpack_from("<I", raw, 12)[0]
    return json.loads(raw[20 : 20 + json_len])


@pytest.mark.skipif(not ROM.is_file(), reason="Ultra Moon test ROM not present")
def test_psyduck_glb_has_skin_and_animations(tmp_path):
    from rae.platforms.threeds.rom import (
        MODEL_GROUP_STRIDE,
        parse_model_header_table,
        read_garc_slot,
    )
    from rae.platforms.threeds.service import build_model_glb

    header = read_garc_slot(ROM, "/a/0/9/4", 0)
    base_group, _count, _flags = parse_model_header_table(header)[54 - 1]
    descriptor = {
        "rom": str(ROM),
        "garc": "/a/0/9/4",
        "group": base_group,
        "base_slot": 1 + base_group * MODEL_GROUP_STRIDE,
        "species": 54,
        "form": 0,
        "name": "Psyduck (#0054, form 00)",
        "type": "model",
    }
    glb_path = build_model_glb(descriptor, tmp_path)
    gltf = _read_glb_json(glb_path)

    # Skin with the full skeleton and skinned primitives.
    assert len(gltf.get("skins", [])) == 1
    skin = gltf["skins"][0]
    assert len(skin["joints"]) == 38
    assert "inverseBindMatrices" in skin
    ibm = gltf["accessors"][skin["inverseBindMatrices"]]
    assert ibm["type"] == "MAT4" and ibm["count"] == len(skin["joints"])
    prim = gltf["meshes"][0]["primitives"][0]
    assert "JOINTS_0" in prim["attributes"] and "WEIGHTS_0" in prim["attributes"]
    for key in ("JOINTS_0", "WEIGHTS_0"):
        acc = gltf["accessors"][prim["attributes"][key]]
        assert acc["count"] == gltf["accessors"][prim["attributes"]["POSITION"]]["count"]
        assert acc["type"] == "VEC4"

    # At least one animation with a plausible duration.
    animations = gltf.get("animations", [])
    assert len(animations) >= 1
    first = animations[0]
    assert first["channels"] and first["samplers"]
    input_acc = gltf["accessors"][first["samplers"][0]["input"]]
    assert input_acc["max"][0] > 0.1
    for channel in first["channels"]:
        assert channel["target"]["node"] < len(skin["joints"])  # bones come first
        assert channel["target"]["path"] in ("translation", "rotation", "scale")
