from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from rae.core.assets import Asset
from rae.platforms.nds.export_module.door_tile import door_animation_semantics
from rae.platforms.nds.export_module.tile_bundle import write_tile_archive
from rae.platforms.nds.gltf.glb_io import GlbData

pytestmark = pytest.mark.nds


def test_door_animation_semantics_prefers_named_open_and_close(tmp_path: Path) -> None:
    glb = tmp_path / "door.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "animations": [{"name": "DoorOpen"}, {"name": "DoorClose"}],
        },
        bin_chunk=b"",
    ).write(glb)

    assert door_animation_semantics(glb) == {
        "clips": ["DoorOpen", "DoorClose"],
        "open": "DoorOpen",
        "close": "DoorClose",
        "closeBehavior": "named",
    }


def test_door_animation_semantics_understands_nitro_clip_suffixes(tmp_path: Path) -> None:
    glb = tmp_path / "nitro-door.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "animations": [
                {"name": "Exact map ambient animations"},
                {"name": "door_c05ht_op"},
                {"name": "door_c05ht_cl"},
            ],
        },
        bin_chunk=b"",
    ).write(glb)

    semantics = door_animation_semantics(glb)
    assert semantics["open"] == "door_c05ht_op"
    assert semantics["close"] == "door_c05ht_cl"
    assert semantics["closeBehavior"] == "named"


def test_tile_writer_preserves_door_authoring_defaults(tmp_path: Path) -> None:
    model = tmp_path / "door.glb"
    GlbData(json={"asset": {"version": "2.0"}}, bin_chunk=b"").write(model)
    asset = Asset(
        asset_id="door-7",
        virtual_path="a/2/7/7/file_0001.bin#door_07.nsbmd",
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0",
        original_data=b"BMD0",
    )
    staging = tmp_path / "staging"
    staging.mkdir()
    output = write_tile_archive(
        tmp_path / "door.tile",
        model_glb=model,
        asset=asset,
        animations=[],
        staging=staging,
        default_tags=["interaction.door"],
        default_properties={
            "interaction.kind": "door",
            "door.animation.open": "DoorOpen",
            "door.animation.close": "reverse",
        },
    )

    with zipfile.ZipFile(output) as archive:
        defaults = json.loads(archive.read("manifest.json"))["defaults"]
    assert defaults["tags"] == ["interaction.door"]
    assert defaults["properties"]["interaction.kind"] == "door"
    assert defaults["properties"]["door.animation.close"] == "reverse"
