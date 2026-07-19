from __future__ import annotations

import struct
from pathlib import Path

import pytest

from rae.platforms.nds.gltf.glb_io import GlbData, read_glb
from rae.platforms.nds.material_animation import (
    _matching_pattern_materials,
    _translation_samples,
    attach_bta0_material_motion,
    attach_btp0_pattern_motion,
    normalize_gen5_shoreline_motion,
    parse_bta0_material_motion,
    parse_btp0_pattern_motion,
    parse_gen5_pattern_container,
)


pytestmark = pytest.mark.nds


def _info_block(values: list[bytes], names: list[str], datum_size: int) -> bytes:
    count = len(values)
    return (
        struct.pack("<BBHHHI", 0, count, 0, 8, 16, 0x17F)
        + b"\0" * (count * 4)
        + struct.pack("<HH", datum_size, 4 + count * datum_size)
        + b"".join(values)
        + b"".join(name.encode("ascii").ljust(16, b"\0") for name in names)
    )


def _sample_bta0(material: str = "water", clip_name: str = "area_water") -> bytes:
    frame_count = 4
    track_info_size = 12 + 4 + 4 + 40 + 16
    sample_offset = 8 + track_info_size
    empty = struct.pack("<HBBI", frame_count, 0, 0x30, 0)
    u = struct.pack("<HBBI", frame_count, 0, 0x10, sample_offset)
    v = struct.pack("<HBBI", frame_count, 0, 0x10, sample_offset + frame_count * 2)
    track = empty + empty + empty + u + v
    track_info = _info_block([track], [material], 40)
    animation = (
        b"M\0AT"
        + struct.pack("<HH", frame_count, 0)
        + track_info
        + struct.pack("<4H", 0, 16, 32, 64)
        + struct.pack("<4H", 0, 32, 64, 96)
    )
    outer_info_size = 12 + 4 + 4 + 4 + 16
    srt = b"SRT0" + b"\0\0\0\0" + _info_block(
        [struct.pack("<I", 8 + outer_info_size)],
        [clip_name],
        4,
    ) + animation
    srt = srt[:4] + struct.pack("<I", len(srt)) + srt[8:]
    header = bytearray(20)
    header[:4] = b"BTA0"
    struct.pack_into("<H", header, 14, 1)
    struct.pack_into("<I", header, 16, 20)
    return bytes(header) + srt


def _sample_btp0(material: str = "rock", clip_name: str = "rock_wave") -> bytes:
    keyframes = struct.pack("<HBBHBB", 0, 0, 0, 6, 1, 1)
    track_info_size = 12 + 4 + 4 + 8 + 16
    keyframe_offset = 12 + track_info_size
    texture_offset = keyframe_offset + len(keyframes)
    palette_offset = texture_offset + 32
    track = struct.pack("<IHH", 2, 0, keyframe_offset)
    animation = (
        b"M\0PT"
        + struct.pack("<HBBHH", 12, 2, 2, texture_offset, palette_offset)
        + _info_block([track], [material], 8)
        + keyframes
        + b"wave.1".ljust(16, b"\0")
        + b"wave.2".ljust(16, b"\0")
        + b"wave.1_pl".ljust(16, b"\0")
        + b"wave.2_pl".ljust(16, b"\0")
    )
    outer_info_size = 12 + 4 + 4 + 4 + 16
    pat = b"PAT0" + b"\0\0\0\0" + _info_block(
        [struct.pack("<I", 8 + outer_info_size)],
        [clip_name],
        4,
    ) + animation
    pat = pat[:4] + struct.pack("<I", len(pat)) + pat[8:]
    header = bytearray(20)
    header[:4] = b"BTP0"
    struct.pack_into("<H", header, 14, 1)
    struct.pack_into("<I", header, 16, 20)
    return bytes(header) + pat


def test_parse_bta0_material_translation_track() -> None:
    clips = parse_bta0_material_motion(_sample_bta0())

    assert len(clips) == 1
    assert clips[0].name == "area_water"
    assert clips[0].frame_count == 4
    assert clips[0].tracks[0].material == "water"
    assert clips[0].tracks[0].frame_offsets == (
        (0.0, 0.0),
        (0.5, 1.0),
        (1.0, 2.0),
        (2.0, 3.0),
    )


def test_gen5_shoreline_uses_bounded_normal_motion_without_lateral_scroll() -> None:
    tracks = [
        {
            "material": "sea_zanami",
            "frameOffsets": [[0.0, -0.2], [16.0, 0.06], [32.0, -0.2]],
        },
        {
            "material": "sea_zanami2",
            "frameOffsets": [[0.0, 0.0], [16.0, 0.0], [32.0, 0.0]],
        },
        {
            "material": "ocean_body",
            "frameOffsets": [[0.0, 0.0], [0.25, 0.0]],
        },
    ]

    assert normalize_gen5_shoreline_motion(tracks) == 2
    assert tracks[0]["frameOffsets"] == [[0.0, -0.2], [0.0, 0.06], [0.0, -0.2]]
    assert tracks[1]["frameOffsets"] == [[0.0, -0.2], [0.0, 0.06], [0.0, -0.2]]
    assert tracks[2]["frameOffsets"] == [[0.0, 0.0], [0.25, 0.0]]


def test_wide_fixed_point_translation_decodes_waterfall_body_motion() -> None:
    samples = struct.pack("<3i", 0, -2048, -4096)
    channel = struct.pack("<HBBI", 3, 0, 0x00, 8)

    assert _translation_samples(b"\0" * 8 + samples, 0, channel) == [0.0, -0.5, -1.0]


def test_parse_btp0_pattern_preserves_material_and_image_names() -> None:
    clips = parse_btp0_pattern_motion(_sample_btp0())

    assert len(clips) == 1
    assert clips[0].name == "rock_wave"
    assert clips[0].frame_count == 12
    assert clips[0].texture_names == ("wave.1", "wave.2")
    assert clips[0].tracks[0].material == "rock"
    assert [(key.frame, key.texture_index) for key in clips[0].tracks[0].keyframes] == [
        (0, 0),
        (6, 1),
    ]


def test_attach_btp0_makes_prop_pattern_playable_and_tile_portable(tmp_path: Path) -> None:
    from PIL import Image

    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(tmp_path / "wave_1.png")
    Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(tmp_path / "wave_2.png")
    glb_path = tmp_path / "rock.glb"
    GlbData(
        json={"asset": {"version": "2.0"}, "materials": [{"name": "rock"}]},
        bin_chunk=b"",
    ).write(glb_path)

    count = attach_btp0_pattern_motion(
        glb_path,
        [_sample_btp0()],
        [tmp_path / "wave_1.png", tmp_path / "wave_2.png"],
    )

    assert count == 1
    motion = read_glb(glb_path).json["extras"]["rae"]["mapMaterialMotion"]
    track = motion["clips"][0]["tracks"][0]
    assert track["material"] == "rock"
    assert track["frameRate"] == 30
    assert [key["frame"] for key in track["imageKeyframes"]] == [0, 6]
    assert all(key["image"].startswith("data:image/png;base64,") for key in track["imageKeyframes"])


def test_attach_bta0_filters_to_materials_in_glb(tmp_path: Path) -> None:
    glb_path = tmp_path / "map.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "water"}],
        },
        bin_chunk=b"",
    ).write(glb_path)

    count = attach_bta0_material_motion(glb_path, [_sample_bta0()])

    assert count == 1
    motion = read_glb(glb_path).json["extras"]["rae"]["mapMaterialMotion"]
    assert motion["frameRate"] == 10
    assert motion["sourceFrameRate"] == 60
    assert motion["defaultClip"] == "area_water"
    assert motion["clips"][0]["tracks"][0]["material"] == "water"
    # Values outside one repeat remain signed/raw so mirrored samplers keep parity.
    assert motion["clips"][0]["tracks"][0]["frameOffsets"][-1] == [2.0, 3.0]


def test_attach_bta0_resolves_apicula_suffix_and_exports_uv_animation(tmp_path: Path) -> None:
    glb_path = tmp_path / "fountain.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [
                {
                    "name": "water_1",
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
                }
            ],
            "textures": [{"source": 0}],
            "images": [{"uri": "missing.png"}],
            "animations": [
                {
                    "name": "area_water",
                    "samplers": [],
                    "channels": [],
                    "extensions": {"EXT_property_animation": {"channels": []}},
                }
            ],
        },
        bin_chunk=b"",
    ).write(glb_path)

    count = attach_bta0_material_motion(glb_path, [_sample_bta0()])

    assert count == 1
    glb = read_glb(glb_path)
    motion = glb.json["extras"]["rae"]["mapMaterialMotion"]
    assert motion["clips"][0]["tracks"][0]["material"] == "water_1"
    assert len(glb.json["animations"]) == 1
    animation = glb.json["animations"][0]
    assert animation["extras"]["rae"]["source"] == "mapMaterialMotion"
    channel = animation["extensions"]["EXT_property_animation"]["channels"][0]
    assert channel["target"].startswith("/materials/0/")
    assert "EXT_property_animation" in glb.json["extensionsUsed"]
    assert glb.json["buffers"][0]["byteLength"] == len(glb.bin_chunk)


def test_attach_bta0_combines_area_and_prop_ambient_tracks(tmp_path: Path) -> None:
    glb_path = tmp_path / "map_with_fountain.glb"
    fountain_bta = _sample_bta0("spray", "area_spray")
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "water"}, {"name": "spray"}],
        },
        bin_chunk=b"",
    ).write(glb_path)

    count = attach_bta0_material_motion(glb_path, [_sample_bta0(), fountain_bta])

    motion = read_glb(glb_path).json["extras"]["rae"]["mapMaterialMotion"]
    assert count == 2
    assert motion["defaultClip"] == "exact_map_ambient"
    assert [track["material"] for track in motion["clips"][0]["tracks"]] == ["water", "spray"]


def test_rock_water_edge_uses_bounded_local_wave_phase(tmp_path: Path) -> None:
    from PIL import Image

    Image.new("RGBA", (16, 16), (255, 255, 255, 255)).save(tmp_path / "edge.png")
    glb_path = tmp_path / "rock_edge.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [
                {
                    "name": "sea_gake02",
                    "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}},
                }
            ],
            "textures": [{"source": 0}],
            "images": [{"uri": "edge.png"}],
        },
        bin_chunk=b"",
    ).write(glb_path)

    attach_bta0_material_motion(glb_path, [_sample_bta0("sea_gake02")])

    motion = read_glb(glb_path).json["extras"]["rae"]["mapMaterialMotion"]
    track = motion["clips"][0]["tracks"][0]
    offsets = track["frameOffsets"]
    assert offsets[-1] == [0.015625, 0.0234375]
    assert track["frameRate"] == 20


def test_pattern_family_does_not_treat_placeholder_material_as_wildcard() -> None:
    materials = ["_1", "hana01_1", "building_wall"]

    assert _matching_pattern_materials(materials, "kawafuchi") == []
    assert _matching_pattern_materials(materials, "hana01.3") == ["hana01_1"]


def test_parse_gen5_pattern_container_preserves_targets_and_keyframes() -> None:
    pattern = bytearray(44)
    struct.pack_into("<III", pattern, 0, 1, 12, 40)
    struct.pack_into("<IHH", pattern, 12, 2, 0, 3)
    pattern[20:22] = b"\x01\x00"
    pattern[24:26] = b"\x02\x00"
    struct.pack_into("<I", pattern, 28, 1)
    pattern[32] = 0
    struct.pack_into("<I", pattern, 36, 5)
    pattern[40:44] = b"BTX0"

    tracks = parse_gen5_pattern_container(bytes(pattern))

    assert len(tracks) == 1
    assert tracks[0].frame_count == 5
    assert [(key.frame, key.texture_index, key.palette_index) for key in tracks[0].keyframes] == [
        (0, 1, 2),
        (3, 0, 0),
    ]
    assert tracks[0].texture_data == b"BTX0"
