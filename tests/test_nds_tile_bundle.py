from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from rae.core.assets import Asset
from rae.platforms.nds.export_module.service import export_options_for
from rae.platforms.nds.export_module.tile_bundle import (
    FORMAT,
    _material_animations,
    write_tile_archive,
)
from rae.platforms.nds.model_module.preview_pipeline import ModelPreviewBundle

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
    )

    with zipfile.ZipFile(output) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert archive.read("model.glb") == b"glTF-model"
        assert manifest["format"] == FORMAT
        animation = manifest["materials"]["animations"][0]
        assert animation["material"] == "water_mat"
        assert animation["frameDurationMs"] == 120
        assert len(animation["frames"]) == 2
        assert archive.read(animation["frames"][1]) == b"png-two"
