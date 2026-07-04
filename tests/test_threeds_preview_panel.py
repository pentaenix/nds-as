"""3DS preview additions: GLB summary parsing, Inc material tint, eye frames."""
from __future__ import annotations

import json
import struct
from pathlib import Path

from rae.platforms.threeds.glb import _is_incandescent_material
from rae.platforms.threeds.motion import EYE_SHEET_COLS, EYE_SHEET_ROWS
from rae.ui.main.threeds_panel import _parse_glb_summary


def _minimal_glb(tmp_path: Path) -> Path:
    png = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63f8ffff3f0005fe02fea72d5e600000000049454e44ae426082"
    )
    doc = {
        "asset": {"version": "2.0"},
        "animations": [{"name": "slot4_00", "channels": [], "samplers": []}],
        "images": [{"bufferView": 0, "mimeType": "image/png", "name": "pm_Eye1.tga"}],
        "textures": [{"source": 0}],
        "materials": [
            {"name": "Eye", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            {"name": "BodyA", "pbrMetallicRoughness": {}},
        ],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(png)}],
        "buffers": [{"byteLength": len(png)}],
    }
    json_bytes = json.dumps(doc).encode()
    json_bytes += b" " * (-len(json_bytes) % 4)
    bin_bytes = png + b"\x00" * (-len(png) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_bytes)
    blob = (
        b"glTF"
        + struct.pack("<II", 2, total)
        + struct.pack("<I", len(json_bytes))
        + b"JSON"
        + json_bytes
        + struct.pack("<I", len(bin_bytes))
        + b"BIN\x00"
        + bin_bytes
    )
    path = tmp_path / "mini.glb"
    path.write_bytes(blob)
    return path


def test_parse_glb_summary_extracts_animations_images_materials(tmp_path):
    summary = _parse_glb_summary(_minimal_glb(tmp_path))
    assert summary["animations"] == ["slot4_00"]
    assert summary["images"][0]["name"] == "pm_Eye1.tga"
    assert summary["images"][0]["png"].startswith(b"\x89PNG")
    assert summary["materials"][0] == {"name": "Eye", "texture": "pm_Eye1.tga"}
    assert summary["materials"][1] == {"name": "BodyA", "texture": ""}


def test_parse_glb_summary_handles_missing_file(tmp_path):
    summary = _parse_glb_summary(tmp_path / "missing.glb")
    assert summary == {"animations": [], "images": [], "materials": []}


def test_incandescent_material_detection():
    assert _is_incandescent_material("BodyANeolant_Inc")
    assert _is_incandescent_material("EyeInc")
    assert not _is_incandescent_material("BodyA")
    assert not _is_incandescent_material("Include")
    assert not _is_incandescent_material("BodyBSpcInc")
    assert not _is_incandescent_material("BodyBInc01")
    assert not _is_incandescent_material("BodyAInc")


def test_eye_frame_grid_constants():
    assert EYE_SHEET_COLS * EYE_SHEET_ROWS == 8
