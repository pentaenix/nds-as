from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

import pytest

from rae.core.assets import Asset
from rae.platforms.nds.export_module.service import export_options_for
from rae.platforms.nds.export_module.tile_bundle import (
    FORMAT,
    _material_animations,
    _material_motion_animations,
    normalize_tile_archive_shoreline_motion,
    write_tile_archive,
)
from rae.platforms.nds.model_module.preview_pipeline import ModelPreviewBundle
from rae.platforms.nds.gltf.glb_io import GlbData, read_glb

pytestmark = pytest.mark.nds


def _asset() -> Asset:
    return Asset(
        asset_id="water-model",
        virtual_path="a/1/2/3/water.nsbmd",
        kind="models",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0",
        original_data=b"BMD0",
    )


def _uv_motion_glb(path: Path, uvs: list[tuple[float, float]]) -> Path:
    positions = [(0.0, 0.0, 0.0), (16.0, 0.0, 0.0), (0.0, 0.0, 16.0), (16.0, 0.0, 16.0)]
    position_bytes = b"".join(struct.pack("<3f", *value) for value in positions)
    uv_bytes = b"".join(struct.pack("<2f", *value) for value in uvs)
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "water", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
            "textures": [{"source": 0, "sampler": 0}],
            "samplers": [{"wrapS": 10497, "wrapT": 33071, "magFilter": 9728, "minFilter": 9728}],
            "images": [{"uri": "water.png"}],
            "buffers": [{"byteLength": len(position_bytes) + len(uv_bytes)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(position_bytes)},
                {"buffer": 0, "byteOffset": len(position_bytes), "byteLength": len(uv_bytes)},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
            ],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "material": 0}]}],
            "nodes": [{"mesh": 0}],
            "scenes": [{"nodes": [0]}],
            "scene": 0,
        },
        bin_chunk=position_bytes + uv_bytes,
    ).write(path)
    return path


def test_bmd0_export_options_include_tile_bundle() -> None:
    keys = [key for key, _label, _description in export_options_for(_asset())]
    assert "tile_bundle" in keys


def test_tile_archive_keeps_glb_and_material_frames(tmp_path: Path) -> None:
    frame_one = tmp_path / "water.1.png"
    frame_two = tmp_path / "water.2.png"
    frame_one.write_bytes(b"png-one")
    frame_two.write_bytes(b"png-two")
    model = tmp_path / "source.glb"
    model.write_bytes(b"glTF-model")
    staging = tmp_path / "staging"
    staging.mkdir()
    bundle = ModelPreviewBundle(
        source_glb=model,
        patched_glb=model,
        mesh_labels=("water_mat",),
        mesh_texture_paths=(frame_one,),
        texture_by_name={"water.1": frame_one, "water.2": frame_two},
        material_to_texture={"water_mat": "water.1"},
        texture_bind_order=("water.1",),
        fallback_paths=(frame_one, frame_two),
    )
    animations = _material_animations(
        {
            "water_mat": {
                "frames": ["water.1", "water.2"],
                "frameDurationMs": 120,
                "loop": True,
            }
        },
        bundle,
        staging,
    )
    output = write_tile_archive(
        tmp_path / "water.tile",
        model_glb=model,
        asset=_asset(),
        animations=animations,
        staging=staging,
        preview_png=b"\x89PNG\r\n\x1a\npreview",
    )

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert archive.read("model.glb") == b"glTF-model"
        assert archive.read("preview.png") == b"\x89PNG\r\n\x1a\npreview"
        assert manifest["format"] == FORMAT
        assert manifest["preview"] == {
            "path": "preview.png",
            "format": "png",
            "projection": "orthographic",
            "view": "top-down",
            "width": 192,
            "height": 192,
        }
        animation = manifest["materials"]["animations"][0]
        assert animation["material"] == "water_mat"
        assert animation["frameDurationMs"] == 120
        assert len(animation["frames"]) == 2
        assert archive.read(animation["frames"][1]) == b"png-two"
        assert manifest["materials"]["uvTextures"] == []


def test_tile_archive_marks_uv_motion_as_world_continuous_texture(tmp_path: Path) -> None:
    model = _uv_motion_glb(tmp_path / "water.glb", [(0, 0), (1, 0), (0, 1), (1, 1)])
    staging = tmp_path / "staging"
    staging.mkdir()
    output = write_tile_archive(
        tmp_path / "water.tile",
        model_glb=model,
        asset=_asset(),
        animations=[{
            "material": "water", "type": "materialMotion", "offsets": [[0, 0], [0.25, 0]],
            "frameCount": 2, "frameDurationMs": 100,
        }],
        staging=staging,
    )
    with zipfile.ZipFile(output) as archive:
        uv_texture = json.loads(archive.read("manifest.json"))["materials"]["uvTextures"][0]
    assert uv_texture == {
        "material": "water",
        "coordinateSpace": "world",
        "sampler": {"wrapS": "repeat", "wrapT": "clamp", "magFilter": "nearest", "minFilter": "nearest"},
    }


def test_tile_archive_keeps_non_affine_shoreline_motion_on_mesh_uvs(tmp_path: Path) -> None:
    model = _uv_motion_glb(tmp_path / "shore.glb", [(0, 0), (1, 0), (0, 1), (2, 2)])
    staging = tmp_path / "staging"
    staging.mkdir()
    output = write_tile_archive(
        tmp_path / "shore.tile",
        model_glb=model,
        asset=_asset(),
        animations=[{
            "material": "water", "type": "materialMotion", "offsets": [[0, 0], [0.25, 0]],
            "frameCount": 2, "frameDurationMs": 100,
        }],
        staging=staging,
    )
    with zipfile.ZipFile(output) as archive:
        uv_texture = json.loads(archive.read("manifest.json"))["materials"]["uvTextures"][0]
    assert uv_texture["coordinateSpace"] == "mesh"


@pytest.mark.parametrize(
    ("alpha_mode", "expected"),
    [("OPAQUE", "opaque"), ("MASK", "cutout"), ("BLEND", "blend")],
)
def test_tile_archive_suggests_editor_render_mode_from_glb(
    tmp_path: Path,
    alpha_mode: str,
    expected: str,
) -> None:
    model = tmp_path / "model.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "surface", "alphaMode": alpha_mode}],
        },
        bin_chunk=b"",
    ).write(model)
    staging = tmp_path / "staging"
    staging.mkdir()

    output = write_tile_archive(
        tmp_path / "surface.tile",
        model_glb=model,
        asset=_asset(),
        animations=[],
        staging=staging,
    )

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["defaults"]["renderMode"] == expected


def test_material_motion_export_uses_per_track_playback_rate(tmp_path: Path) -> None:
    from PIL import Image

    image = tmp_path / "water.png"
    Image.new("RGBA", (4, 4), (20, 80, 200, 180)).save(image)
    model = tmp_path / "water.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [
                {"name": "water", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                {"name": "sea_gake02", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            ],
            "textures": [{"source": 0}],
            "images": [{"uri": "water.png"}],
            "extras": {
                "rae": {
                    "mapMaterialMotion": {
                        "frameRate": 10,
                        "defaultClip": "ambient",
                        "clips": [
                            {
                                "id": "ambient",
                                "tracks": [
                                    {"material": "water", "frameOffsets": [[0, 0], [0.25, 0]]},
                                    {
                                        "material": "sea_gake02",
                                        "frameRate": 20,
                                        "frameOffsets": [[0, 0], [0.125, 0]],
                                    },
                                ],
                            }
                        ],
                    }
                }
            },
        },
        bin_chunk=b"",
    ).write(model)
    staging = tmp_path / "staging"
    staging.mkdir()

    animations = _material_motion_animations(model, staging)

    assert {item["material"]: item["frameDurationMs"] for item in animations} == {
        "water": 100,
        "sea_gake02": 50,
    }
    assert all(item["type"] == "materialMotion" for item in animations)


def test_existing_tile_archive_migrates_manifest_and_embedded_glb_motion(tmp_path: Path) -> None:
    raw_tracks = [
        {
            "material": "sea_zanami",
            "frameOffsets": [[0.0, -0.2], [16.0, 0.06], [32.0, -0.2]],
        },
        {
            "material": "sea_zanami2",
            "frameOffsets": [[0.0, 0.0], [16.0, 0.0], [32.0, 0.0]],
        },
    ]
    model = tmp_path / "model.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "sea_zanami"}, {"name": "sea_zanami2"}],
            "extras": {
                "rae": {
                    "mapMaterialMotion": {
                        "frameRate": 10,
                        "defaultClip": "shore",
                        "clips": [{"id": "shore", "tracks": raw_tracks}],
                    }
                }
            },
        },
        bin_chunk=b"",
    ).write(model)
    manifest = {
        "format": FORMAT,
        "materials": {
            "animations": [
                {
                    "material": track["material"],
                    "type": "materialMotion",
                    "offsets": track["frameOffsets"],
                }
                for track in raw_tracks
            ]
        },
    }
    tile_path = tmp_path / "shore.tile"
    with zipfile.ZipFile(tile_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.write(model, "model.glb")
        archive.writestr("preview.png", b"preview")

    assert normalize_tile_archive_shoreline_motion(tile_path) == 4

    with zipfile.ZipFile(tile_path) as archive:
        migrated = json.loads(archive.read("manifest.json"))
        migrated_model = tmp_path / "migrated.glb"
        migrated_model.write_bytes(archive.read("model.glb"))
        assert archive.read("preview.png") == b"preview"
    expected = [[0.0, -0.2], [0.0, 0.06], [0.0, -0.2]]
    assert [item["offsets"] for item in migrated["materials"]["animations"]] == [expected, expected]
    glb_tracks = (
        read_glb(migrated_model).json["extras"]["rae"]["mapMaterialMotion"]["clips"][0]["tracks"]
    )
    assert [track["frameOffsets"] for track in glb_tracks] == [expected, expected]
    assert normalize_tile_archive_shoreline_motion(tile_path) == 0
