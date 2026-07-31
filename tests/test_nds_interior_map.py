from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
import trimesh

from src.platforms.nds.export_module.interior_map import (
    build_gen5_interior_metadata,
    classify_interior_material,
    export_gen5_interior_kit,
    export_gen5_interior_package,
    is_gen5_interior_candidate,
)
from src.platforms.nds.gltf.glb_io import read_glb


@pytest.mark.nds
def test_interior_material_roles_are_conservative() -> None:
    assert classify_interior_material("fhkabe2_1") == "wall"
    assert classify_interior_material("fhyuka_1") == "floor"
    assert classify_interior_material("fhgenkan_1") == "entrance"
    assert classify_interior_material("h_kage") == "shadow"
    assert classify_interior_material("h_mado") == "window"
    assert classify_interior_material("in40_kaidan01") == "stairs"


def _box(name: str, extents: tuple[float, float, float], translation: tuple[float, float, float]):
    mesh = trimesh.creation.box(extents=extents)
    mesh.apply_translation(translation)
    mesh.visual = trimesh.visual.TextureVisuals(
        material=trimesh.visual.material.PBRMaterial(name=name)
    )
    return mesh


@pytest.mark.nds
def test_interior_package_contains_grid_masks_and_embedded_metadata(tmp_path) -> None:
    scene = trimesh.Scene()
    scene.add_geometry(_box("fhyuka_1", (64, 1, 48), (32, -0.5, 24)), geom_name="floor")
    scene.add_geometry(_box("fhgenkan_1", (16, 1, 16), (32, -0.4, 40)), geom_name="entrance")
    scene.add_geometry(_box("fhkabe_1", (64, 32, 1), (32, 16, 0)), geom_name="wall")
    scene.add_geometry(_box("fhkabe_1", (64, 32, 1), (32, 16, 40)), geom_name="south_wall")
    source = tmp_path / "source.glb"
    source.write_bytes(scene.export(file_type="glb"))

    metadata = build_gen5_interior_metadata(
        source,
        map_file_index=842,
        area_index=284,
        virtual_path="a/0/0/8/file_0842.bin#carved_0x14.nsbmd",
        rom_name="Pokemon Black 2",
    )
    assert metadata["tileSize"] == 16
    assert metadata["gridSize"] == [4, 3]
    assert metadata["materialRoles"]["wall"] == ["fhkabe_1"]
    assert any(any(row) for row in metadata["walkableMask"])
    assert metadata["floorDatum"] == pytest.approx(0.0)
    assert metadata["primaryFloorDatum"] == pytest.approx(0.0)
    assert metadata["heightStep"] == 16
    assert metadata["heightMask"] == [[0] * 4 for _ in range(3)]
    assert metadata["collisionInsetTiles"] == 1
    assert metadata["blockedMask"][0] == [1, 1, 1, 1]
    assert metadata["blockedMask"][2] == [1, 1, 0, 1]

    composition = SimpleNamespace(
        terrain_glb=source,
        objects=SimpleNamespace(map_file_index=842, area=SimpleNamespace(index=284)),
    )
    glb_path, metadata_path = export_gen5_interior_package(
        composition, tmp_path / "out", rom_name="Pokemon Black 2"
    )
    assert json.loads(metadata_path.read_text())["source"]["mapFileIndex"] == 842
    embedded = read_glb(glb_path).json["extras"]["rae"]["interiorMap"]
    assert embedded["entrance"]["edge"] == metadata["entrance"]["edge"]
    assert is_gen5_interior_candidate(glb_path)

    manifest_path, parts = export_gen5_interior_kit(glb_path, metadata, tmp_path / "kit")
    manifest = json.loads(manifest_path.read_text())
    assert manifest["format"] == "rae.gen5InteriorKit"
    assert {part["role"] for part in manifest["parts"]} == {"floor", "entrance", "wall"}
    assert len(parts) == 3
    assert all(path.is_file() for path in parts)
    assert (manifest_path.parent / "interior-kit.zip").is_file()
    assert all(
        read_glb(path).json["extras"]["rae"]["interiorKitPart"]["source"]["mapFileIndex"] == 842
        for path in parts
    )
    exported_scene = trimesh.load(glb_path, force="scene", process=False)
    wall_normals = []
    wall_centers_z = []
    black_cap_normals = []
    for geometry in exported_scene.geometry.values():
        material = getattr(getattr(geometry, "visual", None), "material", None)
        if getattr(material, "name", "") == "fhkabe_1":
            wall_normals.extend(geometry.face_normals)
            wall_centers_z.extend(geometry.triangles_center[:, 2])
        if getattr(material, "name", "").startswith("rae_interior_wall_top_black__"):
            black_cap_normals.extend(geometry.face_normals)
    assert wall_normals
    assert all(abs(float(normal[1])) <= 0.35 for normal in wall_normals)
    assert wall_centers_z and max(float(value) for value in wall_centers_z) < 32.0
    assert black_cap_normals
    assert all(float(normal[1]) >= 0.7 for normal in black_cap_normals)


@pytest.mark.nds
def test_multi_height_interior_emits_quantized_collision_heights(tmp_path) -> None:
    scene = trimesh.Scene()
    scene.add_geometry(_box("in40_yuka01", (16, 1, 16), (8, -0.5, 8)), geom_name="low")
    scene.add_geometry(_box("in40_kaidan01", (16, 1, 16), (24, 7.5, 8)), geom_name="step")
    scene.add_geometry(_box("in40_yuka02", (16, 1, 16), (40, 15.5, 8)), geom_name="high")
    source = tmp_path / "levels.glb"
    source.write_bytes(scene.export(file_type="glb"))

    metadata = build_gen5_interior_metadata(
        source,
        map_file_index=860,
        area_index=321,
        virtual_path="a/0/0/8/file_0860.bin#carved_0x14.nsbmd",
        rom_name="Pokemon Black 2",
    )

    assert metadata["floorDatum"] == pytest.approx(0.0)
    assert metadata["heightStep"] == 8
    assert metadata["heightMask"] == [[0, 1, 2]]
    assert metadata["ambiguousFloorCells"] == []
