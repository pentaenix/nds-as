from __future__ import annotations

from pathlib import Path

import pytest

from rae.platforms.nds.gltf.compose import GlbScenePart, compose_glb_scenes
from rae.platforms.nds.gltf.glb_io import GlbData, read_glb


pytestmark = pytest.mark.nds


def _scene(path: Path, node_name: str) -> None:
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"name": node_name}],
            "buffers": [{"byteLength": 4}],
        },
        bin_chunk=b"test",
    ).write(path)


def test_compose_glb_scenes_adds_placement_parents_without_numpy(tmp_path: Path) -> None:
    terrain = tmp_path / "terrain.glb"
    building = tmp_path / "building.glb"
    _scene(terrain, "terrain_node")
    _scene(building, "building_node")

    output = compose_glb_scenes(
        [
            GlbScenePart(terrain, "terrain"),
            GlbScenePart(building, "house", translation=(12, 3, -8), quarter_turns=1),
        ],
        tmp_path / "composed.glb",
    )

    gltf = read_glb(output).json
    house = next(node for node in gltf["nodes"] if node.get("name") == "house")
    assert house["matrix"][12:15] == [12.0, 3.0, -8.0]
    assert house["matrix"][:4] == [0.0, 0.0, -1.0, 0.0]
    assert len(gltf["scenes"][0]["nodes"]) == 2


def test_compose_glb_scenes_accepts_free_y_rotation(tmp_path: Path) -> None:
    building = tmp_path / "building.glb"
    _scene(building, "building_node")

    output = compose_glb_scenes(
        [GlbScenePart(building, "boat", rotation_degrees=45.0)],
        tmp_path / "composed.glb",
    )

    boat = next(node for node in read_glb(output).json["nodes"] if node.get("name") == "boat")
    assert boat["matrix"][0] == pytest.approx(2**-0.5)
    assert boat["matrix"][2] == pytest.approx(-(2**-0.5))
