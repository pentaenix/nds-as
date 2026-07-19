from pathlib import Path
import io
import struct

import pytest
import trimesh
from PIL import Image

from rae.core.assets import Asset
from rae.platforms.nds.export_module.tile_extract import (
    PreviewTileBatchItem,
    export_preview_material_occurrences_as_tiles,
)
from rae.platforms.nds.export_module.tile_extract import render_preview_materials_png
from rae.platforms.nds.export_module.tile_bundle import (
    _CLAMP_TO_EDGE,
    _material_motion_animations,
    _motion_frame_image,
)
from rae.platforms.nds.gltf.extract import (
    MaterialComponent,
    choose_logical_tile_anchor,
    cluster_spatial_components,
    extract_material_primitives,
    group_logical_tile_materials,
    is_puddle_material,
    is_shadow_material,
    is_waterfall_material,
    list_material_components,
    logical_tree_family,
    recenter_glb_geometry,
    shoreline_tile_occurrences,
    spatial_assembly_materials,
    spatial_tile_occurrences,
    suggest_logical_materials,
    suggest_spatial_feature_bounds,
    suggest_tile_surface_origin_y,
)
from rae.platforms.nds.gltf.glb_io import GlbData, read_glb


pytestmark = pytest.mark.nds


def test_tree_material_layers_form_one_logical_extractor_row() -> None:
    names = (
        "grass01ax",
        "ki02ax",
        "ki02bx",
        "ki02c",
        "ki02dx",
        "kusa_ec1",
        "kusa_ec2",
        "kusa_ec3",
        "shore01",
    )

    assert logical_tree_family("ki02ax") == "ki02"
    assert group_logical_tile_materials(names) == (
        ("grass01ax",),
        ("ki02ax", "ki02bx", "ki02c", "ki02dx"),
        ("kusa_ec1", "kusa_ec2", "kusa_ec3"),
        ("shore01",),
    )


def test_logical_object_uses_lowest_horizontal_footprint_as_anchor() -> None:
    def component(material: str, y: float, extents: tuple[float, float, float]) -> MaterialComponent:
        return MaterialComponent(
            material=material,
            index=0,
            mesh_index=0,
            primitive_index=0,
            triangle_count=2,
            bounds_min=(0.0, y, 0.0),
            bounds_max=(extents[0], y + extents[1], extents[2]),
            uv_span=(2.0, 1.0),
        )

    catalog = {
        "ki02ax": (component("ki02ax", 3.0, (0.0, 53.0, 64.0)),),
        "ki02bx": (component("ki02bx", 22.0, (64.0, 0.0, 32.0)),),
        "ki02c": (component("ki02c", 3.0, (64.0, 0.0, 32.0)),),
        "ki02dx": (component("ki02dx", 46.5, (64.0, 0.0, 32.0)),),
    }

    assert choose_logical_tile_anchor(
        catalog,
        ("ki02ax", "ki02bx", "ki02c", "ki02dx"),
    ) == "ki02c"


def test_kusa_uv_scale_yields_one_grass_per_occurrence() -> None:
    component = MaterialComponent(
        material="kusa_ec3",
        index=0,
        mesh_index=0,
        primitive_index=0,
        triangle_count=8,
        bounds_min=(-32.0, 2.0, -32.0),
        bounds_max=(0.0, 2.0, 0.0),
        uv_span=(2.0, 2.0),
    )

    occurrences = spatial_tile_occurrences((component,))

    assert len(occurrences) == 4
    assert all(entry.extents == pytest.approx((16.0, 0.0, 16.0)) for entry in occurrences)


def test_grass_and_tree_assembly_never_adds_the_terrain_floor() -> None:
    def component(material: str, y: float) -> MaterialComponent:
        return MaterialComponent(
            material=material,
            index=0,
            mesh_index=0,
            primitive_index=0,
            triangle_count=2,
            bounds_min=(0.0, y, 0.0),
            bounds_max=(16.0, y, 16.0),
            uv_span=(1.0, 1.0),
        )

    catalog = {
        "kusa_ec1": (component("kusa_ec1", 8.0),),
        "kusa_ec2": (component("kusa_ec2", 6.0),),
        "kusa_ec3": (component("kusa_ec3", 2.0),),
        "grass_ground": (component("grass_ground", 0.0),),
        "upper_floor": (component("upper_floor", 96.0),),
    }

    assembled = spatial_assembly_materials(
        catalog,
        ("kusa_ec1", "kusa_ec2", "kusa_ec3"),
        (0.0, 0.0, 16.0, 16.0),
        (2.0, 2.0),
    )

    assert assembled == ("kusa_ec1", "kusa_ec2", "kusa_ec3")


def test_puddle_assembly_adds_deep_reflection_but_not_ground_or_shadow() -> None:
    def component(material: str, y: float) -> MaterialComponent:
        return MaterialComponent(
            material=material,
            index=0,
            mesh_index=0,
            primitive_index=0,
            triangle_count=2,
            bounds_min=(0.0, y, 0.0),
            bounds_max=(48.0, y, 48.0),
            uv_span=(1.0, 1.0),
        )

    catalog = {
        "mizutama01": (component("mizutama01", 0.0),),
        "mizu_sita2": (component("mizu_sita2", -32.0),),
        "grass01ax": (component("grass01ax", 0.0),),
        "h_kage": (component("h_kage", 2.0),),
    }

    assembled = spatial_assembly_materials(
        catalog,
        ("mizutama01",),
        (0.0, 0.0, 48.0, 48.0),
        (0.0, 0.0),
    )

    assert is_puddle_material("mizutama01")
    assert is_shadow_material("h_kage")
    assert assembled == ("mizutama01", "mizu_sita2")


def test_shore_assembly_keeps_edge_layers_separate_from_water_body() -> None:
    def component(material: str, y: float) -> MaterialComponent:
        return MaterialComponent(
            material=material,
            index=0,
            mesh_index=0,
            primitive_index=0,
            triangle_count=2,
            bounds_min=(0.0, y, 0.0),
            bounds_max=(48.0, y, 48.0),
            uv_span=(1.0, 1.0),
        )

    catalog = {
        "sea_simi_1": (component("sea_simi_1", -80.0),),
        "sea_zanami": (component("sea_zanami", -87.0),),
        "sea_zanami2": (component("sea_zanami2", -87.0),),
        "sea_mizu1": (component("sea_mizu1", -104.0),),
        "sea_mizu1_1": (component("sea_mizu1_1", -91.0),),
        "sea_jimen": (component("sea_jimen", -80.0),),
    }

    assembled = spatial_assembly_materials(
        catalog,
        ("sea_simi_1", "sea_zanami", "sea_zanami2"),
        (0.0, 0.0, 48.0, 48.0),
        (-89.0, -80.0),
        feature_kind="shore",
    )

    assert assembled == ("sea_simi_1", "sea_zanami", "sea_zanami2")
    assert suggest_tile_surface_origin_y(catalog, assembled) == -80.0


def test_shadow_and_loose_rock_rows_do_not_collect_overlapping_scene_geometry() -> None:
    def component(material: str) -> MaterialComponent:
        return MaterialComponent(
            material=material,
            index=0,
            mesh_index=0,
            primitive_index=0,
            triangle_count=2,
            bounds_min=(0.0, 0.0, 0.0),
            bounds_max=(16.0, 8.0, 16.0),
            uv_span=(1.0, 1.0),
        )

    catalog = {
        name: (component(name),)
        for name in ("h_kage", "michi_isi", "grass01ax", "building_window")
    }

    assert spatial_assembly_materials(
        catalog, ("h_kage",), (0.0, 0.0, 16.0, 16.0), (0.0, 8.0)
    ) == ("h_kage",)
    assert spatial_assembly_materials(
        catalog, ("michi_isi",), (0.0, 0.0, 16.0, 16.0), (0.0, 8.0)
    ) == ("michi_isi",)


def test_beach_occurrences_are_one_by_three_straights_and_three_by_three_corners() -> None:
    straight = MaterialComponent(
        material="sea_simi_1",
        index=0,
        mesh_index=0,
        primitive_index=0,
        triangle_count=20,
        bounds_min=(96.0, -89.0, -76.0),
        bounds_max=(256.0, -80.0, -48.0),
        uv_span=(10.0, 1.0),
    )
    corner_parts = (
        MaterialComponent(
            material="sea_simi_1",
            index=1,
            mesh_index=0,
            primitive_index=0,
            triangle_count=4,
            bounds_min=(48.0, -89.0, -96.0),
            bounds_max=(76.0, -80.0, -48.0),
            uv_span=(2.0, 1.0),
        ),
        MaterialComponent(
            material="sea_simi_1",
            index=2,
            mesh_index=0,
            primitive_index=0,
            triangle_count=4,
            bounds_min=(48.0, -89.0, -76.0),
            bounds_max=(96.0, -80.0, -48.0),
            uv_span=(2.0, 1.0),
        ),
    )

    occurrences = shoreline_tile_occurrences((straight, *corner_parts))

    straights = [entry for entry in occurrences if entry.extents[0] == pytest.approx(16.0)]
    corners = [entry for entry in occurrences if entry.extents[0] == pytest.approx(48.0)]
    assert len(straights) == 10
    assert all(entry.extents == pytest.approx((16.0, 9.0, 48.0)) for entry in straights)
    assert len(corners) == 1
    assert corners[0].bounds_min == pytest.approx((48.0, -89.0, -96.0))
    assert corners[0].bounds_max == pytest.approx((96.0, -80.0, -48.0))


def test_disconnected_cave_faces_cluster_into_complete_object_occurrences() -> None:
    def component(index: int, bounds: tuple[float, float, float, float, float, float]) -> MaterialComponent:
        return MaterialComponent(
            material="doukutu01",
            index=index,
            mesh_index=0,
            primitive_index=0,
            triangle_count=2,
            bounds_min=bounds[:3],
            bounds_max=bounds[3:],
            uv_span=(1.0, 1.0),
        )

    components = (
        component(0, (-164.0, 2.0, 128.0, -108.0, 28.0, 144.0)),
        component(1, (-164.0, 2.0, 112.0, -108.0, 2.0, 128.0)),
        component(2, (-120.0, 12.0, 118.0, -104.0, 24.0, 132.0)),
        component(3, (64.0, 2.0, -32.0, 120.0, 28.0, -16.0)),
    )

    occurrences = cluster_spatial_components(components)

    assert len(occurrences) == 2
    assert occurrences[1].bounds_min == pytest.approx((-164.0, 2.0, 112.0))
    assert occurrences[1].bounds_max == pytest.approx((-104.0, 28.0, 144.0))


def test_long_connected_strip_becomes_navigable_map_tiles() -> None:
    component = MaterialComponent(
        material="shore01",
        index=0,
        mesh_index=0,
        primitive_index=0,
        triangle_count=12,
        bounds_min=(64.0, -2.0, 128.0),
        bounds_max=(160.0, -2.0, 160.0),
        uv_span=(3.0, 1.0),
    )

    occurrences = spatial_tile_occurrences((component,))

    assert len(occurrences) == 3
    assert [entry.bounds_min for entry in occurrences] == [
        (64.0, -2.0, 128.0),
        (96.0, -2.0, 128.0),
        (128.0, -2.0, 128.0),
    ]
    assert all(entry.extents == pytest.approx((32.0, 0.0, 32.0)) for entry in occurrences)


def _write_two_quad_glb(path: Path, *, second_offset: float = 96.0) -> None:
    positions = struct.pack(
        "<24f",
        0.0, 0.0, 0.0,
        0.0, 0.0, 32.0,
        32.0, 0.0, 32.0,
        32.0, 0.0, 0.0,
        second_offset, 0.0, 0.0,
        second_offset, 0.0, 32.0,
        second_offset + 32.0, 0.0, 32.0,
        second_offset + 32.0, 0.0, 0.0,
    )
    uvs = struct.pack("<16f", 0, 0, 0, 1, 1, 1, 1, 0, 0, 0, 0, 1, 1, 1, 1, 0)
    indices = struct.pack("<12H", 0, 1, 2, 0, 2, 3, 4, 5, 6, 4, 6, 7)
    position_end = len(positions)
    uv_end = position_end + len(uvs)
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "materials": [{"name": "tree"}],
            "meshes": [{"primitives": [{
                "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                "indices": 2,
                "material": 0,
            }]}],
            "buffers": [{"byteLength": len(positions) + len(uvs) + len(indices)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                {"buffer": 0, "byteOffset": position_end, "byteLength": len(uvs)},
                {"buffer": 0, "byteOffset": uv_end, "byteLength": len(indices)},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"},
                {"bufferView": 1, "componentType": 5126, "count": 8, "type": "VEC2"},
                {"bufferView": 2, "componentType": 5123, "count": 12, "type": "SCALAR"},
            ],
        },
        bin_chunk=positions + uvs + indices,
    ).write(path)


def test_extract_material_primitives_keeps_only_selected_material(tmp_path: Path) -> None:
    source = tmp_path / "map.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [
                {"name": "grass", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
                {"name": "pond", "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}},
                {"name": "cliff", "pbrMetallicRoughness": {"baseColorTexture": {"index": 2}}},
            ],
            "textures": [{"source": 0}, {"source": 1}, {"source": 2}],
            "images": [{"uri": "grass.png"}, {"uri": "pond.png"}, {"uri": "cliff.png"}],
            "extras": {"rae": {"mapMaterialMotion": {
                "defaultClip": "water",
                "clips": [{"id": "water", "tracks": [
                    {"material": "pond", "frameOffsets": [[0, 0], [0.5, 0]]},
                    {"material": "cliff", "frameOffsets": [[0, 0], [0, 0.5]]},
                ]}],
            }}},
            "meshes": [{
                "name": "map",
                "primitives": [
                    {"attributes": {"POSITION": 0}, "material": 0},
                    {"attributes": {"POSITION": 1}, "material": 1},
                    {"attributes": {"POSITION": 2}, "material": 2},
                ],
            }],
        },
        bin_chunk=b"",
    ).write(source)

    output = extract_material_primitives(source, tmp_path / "pond.glb", ["pond"])

    gltf = read_glb(output).json
    assert [material["name"] for material in gltf["materials"]] == ["pond"]
    assert gltf["meshes"][0]["primitives"] == [
        {"attributes": {"POSITION": 1}, "material": 0}
    ]
    assert gltf["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["index"] == 0
    assert gltf["textures"] == [{"source": 0}]
    assert gltf["images"] == [{"uri": "pond.png"}]
    tracks = gltf["extras"]["rae"]["mapMaterialMotion"]["clips"][0]["tracks"]
    assert [track["material"] for track in tracks] == ["pond"]
    property_target = gltf["animations"][0]["extensions"]["EXT_property_animation"]["channels"][0]["target"]
    assert property_target.startswith("/materials/0/")


def test_extract_material_primitives_rejects_unknown_material(tmp_path: Path) -> None:
    source = tmp_path / "map.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "materials": [{"name": "grass"}],
            "meshes": [{"primitives": [{"attributes": {}, "material": 0}]}],
        },
        bin_chunk=b"",
    ).write(source)

    with pytest.raises(ValueError, match="Available: grass"):
        extract_material_primitives(source, tmp_path / "missing.glb", ["pond"])


def test_tile_extraction_selects_one_disconnected_material_instance(tmp_path: Path) -> None:
    source = tmp_path / "trees.glb"
    _write_two_quad_glb(source)

    components = list_material_components(source)["tree"]
    assert len(components) == 2
    assert components[0].bounds_min == (0.0, 0.0, 0.0)
    assert components[1].bounds_min == (96.0, 0.0, 0.0)

    output = extract_material_primitives(
        source,
        tmp_path / "one-tree.glb",
        ["tree"],
        component_indices={"tree": 1},
        recenter=True,
    )

    extracted = list_material_components(output)["tree"]
    assert len(extracted) == 1
    assert extracted[0].bounds_min == (96.0, 0.0, 0.0)
    selection = read_glb(output).json["extras"]["rae"]["tileSelection"]
    assert selection["components"] == {"tree": 1}


def test_batch_export_writes_every_spatial_occurrence_with_its_identity(tmp_path: Path) -> None:
    source = tmp_path / "shore.glb"
    _write_two_quad_glb(source, second_offset=32.0)
    asset = Asset(
        asset_id="file-0263",
        virtual_path="a/0/0/8/file_0263.bin#carved_0x14.nsbmd",
        kind="models",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0",
        original_data=b"BMD0",
    )
    items = [
        PreviewTileBatchItem(
            name="Shore straight 01",
            filename="shore_straight_01.tile",
            selected_materials=("tree",),
            spatial_tile_bounds=(0.0, 0.0, 32.0, 32.0),
            footprint=(2, 2),
        ),
        PreviewTileBatchItem(
            name="Shore straight 02",
            filename="shore_straight_02.tile",
            selected_materials=("tree",),
            spatial_tile_bounds=(32.0, 0.0, 64.0, 32.0),
            footprint=(2, 2),
        ),
    ]

    written = export_preview_material_occurrences_as_tiles(
        asset=asset,
        source_glb=source,
        items=items,
        output_dir=tmp_path / "tiles",
        mesh_labels=["tree"],
        mesh_texture_paths=[None],
        texture_by_name={},
        fallback_paths=[],
        material_to_texture={},
        texture_bind_order=[],
        material_specs={},
    )

    assert [path.name for path in written] == [
        "shore_straight_01.tile",
        "shore_straight_02.tile",
    ]
    import json
    import zipfile

    with zipfile.ZipFile(written[1]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        model = tmp_path / "second.glb"
        model.write_bytes(archive.read("model.glb"))
    assert manifest["name"] == "Shore straight 02"
    assert manifest["source"]["batchOccurrence"] == 2
    assert manifest["source"]["batchCount"] == 2
    assert manifest["source"]["footprint"] == {"width": 2, "height": 2}
    component = list_material_components(model)["tree"][0]
    assert component.bounds_min == pytest.approx((32.0, 0.0, 0.0))
    assert component.bounds_max == pytest.approx((64.0, 0.0, 32.0))


def test_tile_extraction_reduces_repeated_plane_to_one_uv_unit(tmp_path: Path) -> None:
    source = tmp_path / "water-strip.glb"
    _write_two_quad_glb(source, second_offset=32.0)
    glb = read_glb(source)
    # Make the first connected 64x32 surface repeat twice across U.
    glb.json["accessors"][0]["count"] = 4
    glb.json["accessors"][1]["count"] = 4
    glb.json["accessors"][2]["count"] = 6
    uv_view = glb.json["bufferViews"][1]
    uv_offset = int(uv_view["byteOffset"])
    data = bytearray(glb.bin_chunk)
    data[uv_offset : uv_offset + 32] = struct.pack("<8f", 0, 0, 0, 1, 2, 1, 2, 0)
    # Stretch that quad to 64 world units.
    data[:48] = struct.pack(
        "<12f",
        0, 0, 0,
        0, 0, 32,
        64, 0, 32,
        64, 0, 0,
    )
    glb.bin_chunk = bytes(data)
    glb.write(source)

    output = extract_material_primitives(
        source,
        tmp_path / "water-tile.glb",
        ["tree"],
        component_indices={"tree": 0},
        repeat_patch_materials={"tree"},
    )

    component = list_material_components(output)["tree"][0]
    assert component.extents == pytest.approx((32.0, 0.0, 32.0))
    assert component.uv_span == pytest.approx((1.0, 1.0))

    second_cell = extract_material_primitives(
        source,
        tmp_path / "water-second-cell.glb",
        ["tree"],
        spatial_tile_bounds=(32.0, 0.0, 64.0, 32.0),
    )
    second_component = list_material_components(second_cell)["tree"][0]
    assert second_component.bounds_min == pytest.approx((32.0, 0.0, 0.0))
    assert second_component.bounds_max == pytest.approx((64.0, 0.0, 32.0))


def test_spatial_crop_preserves_diagonal_shore_shape(tmp_path: Path) -> None:
    source = tmp_path / "diagonal-shore.glb"
    positions = struct.pack(
        "<9f",
        0.0, 0.0, 0.0,
        32.0, 0.0, 0.0,
        0.0, 0.0, 32.0,
    )
    uvs = struct.pack("<6f", 0.0, 0.0, 2.0, 0.0, 0.0, 2.0)
    indices = struct.pack("<3H", 0, 1, 2)
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "materials": [{"name": "animated_shore"}],
            "meshes": [{"primitives": [{
                "attributes": {"POSITION": 0, "TEXCOORD_0": 1},
                "indices": 2,
                "material": 0,
            }]}],
            "buffers": [{"byteLength": len(positions) + len(uvs) + len(indices)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                {"buffer": 0, "byteOffset": len(positions), "byteLength": len(uvs)},
                {
                    "buffer": 0,
                    "byteOffset": len(positions) + len(uvs),
                    "byteLength": len(indices),
                },
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                {"bufferView": 1, "componentType": 5126, "count": 3, "type": "VEC2"},
                {"bufferView": 2, "componentType": 5123, "count": 3, "type": "SCALAR"},
            ],
        },
        bin_chunk=positions + uvs + indices,
    ).write(source)

    output = extract_material_primitives(
        source,
        tmp_path / "one-shore-cell.glb",
        ["animated_shore"],
        spatial_tile_bounds=(16.0, 0.0, 32.0, 16.0),
    )

    loaded = trimesh.load(output, force="scene")
    mesh = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    xz = {(round(float(vertex[0]), 4), round(float(vertex[2]), 4)) for vertex in mesh.vertices}
    assert xz == {(16.0, 0.0), (32.0, 0.0), (16.0, 16.0)}
    assert (32.0, 16.0) not in xz


def test_spatial_tile_combines_tree_layers_and_preserves_height(tmp_path: Path) -> None:
    source = tmp_path / "layered-tree.glb"
    horizontal = struct.pack(
        "<12f",
        0, 3, 0, 0, 3, 64, 64, 3, 64, 64, 3, 0,
    )
    vertical = struct.pack(
        "<12f",
        0, 3, 16, 0, 56, 16, 64, 56, 16, 64, 3, 16,
    )
    uvs = struct.pack("<8f", 0, 0, 0, 2, 2, 2, 2, 0)
    vertical_uvs = struct.pack("<8f", 0, 0, 0, 1, 2, 1, 2, 0)
    indices = struct.pack("<6H", 0, 1, 2, 0, 2, 3)
    chunks = [horizontal, uvs, indices, vertical, vertical_uvs, indices]
    offsets = []
    cursor = 0
    for chunk in chunks:
        offsets.append(cursor)
        cursor += len(chunk)
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "materials": [{"name": "ki02c"}, {"name": "ki02ax"}],
            "meshes": [{"primitives": [
                {"attributes": {"POSITION": 0, "TEXCOORD_0": 1}, "indices": 2, "material": 0},
                {"attributes": {"POSITION": 3, "TEXCOORD_0": 4}, "indices": 5, "material": 1},
            ]}],
            "buffers": [{"byteLength": cursor}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": offset, "byteLength": len(chunk)}
                for offset, chunk in zip(offsets, chunks)
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                {"bufferView": 1, "componentType": 5126, "count": 4, "type": "VEC2"},
                {"bufferView": 2, "componentType": 5123, "count": 6, "type": "SCALAR"},
                {"bufferView": 3, "componentType": 5126, "count": 4, "type": "VEC3"},
                {"bufferView": 4, "componentType": 5126, "count": 4, "type": "VEC2"},
                {"bufferView": 5, "componentType": 5123, "count": 6, "type": "SCALAR"},
            ],
        },
        bin_chunk=b"".join(chunks),
    ).write(source)

    assert suggest_logical_materials(["grass", "ki02c", "ki02ax"], "ki02ax") == ["ki02c", "ki02ax"]
    output = extract_material_primitives(
        source,
        tmp_path / "whole-tree.glb",
        ["ki02c", "ki02ax"],
        recenter=True,
        spatial_tile_bounds=(0.0, 0.0, 32.0, 32.0),
    )

    components = list_material_components(output)
    assert components["ki02c"][0].extents == pytest.approx((32.0, 0.0, 32.0))
    assert components["ki02ax"][0].extents == pytest.approx((32.0, 53.0, 0.0))
    bounds = read_glb(output).json["extras"]["rae"]["tileBounds"]
    assert bounds["min"] == [0.0, 3.0, 0.0]
    assert bounds["max"] == [32.0, 56.0, 32.0]
    assert bounds["originTranslation"] == [-16.0, -3.0, -16.0]

    complete_output = extract_material_primitives(
        source,
        tmp_path / "complete-tree.glb",
        ["ki02c", "ki02ax"],
        recenter=True,
        spatial_tile_bounds=(16.0, 0.0, 48.0, 32.0),
        preserve_spatial_components=True,
        spatial_component_center_filter=True,
    )
    complete = list_material_components(complete_output)
    assert complete["ki02c"][0].extents == pytest.approx((64.0, 0.0, 64.0))
    assert complete["ki02ax"][0].extents == pytest.approx((64.0, 53.0, 0.0))
    selection = read_glb(complete_output).json["extras"]["rae"]["tileSelection"]
    assert selection["preserveSpatialComponents"] is True
    assert selection["spatialComponentCenterFilter"] is True


def test_waterfall_layers_form_one_padded_logical_feature() -> None:
    names = [
        "grass01ax",
        "kawa01a",
        "kawa01b",
        "kawa01b_1",
        "kawa_soko",
        "taki_sakai_1",
        "taki_shibu_1",
    ]
    component = MaterialComponent(
        material="kawa01b",
        index=0,
        mesh_index=0,
        primitive_index=0,
        triangle_count=6,
        bounds_min=(160.0, -8.0, -128.0),
        bounds_max=(240.0, 72.0, -112.0),
    )

    assert is_waterfall_material("taki_shibu_1")
    assert suggest_logical_materials(names, "kawa01b") == names[1:]
    assert suggest_spatial_feature_bounds(component) == (152.0, -136.0, 248.0, -104.0)


def test_extract_material_primitives_recenters_tile_to_ground_origin(tmp_path: Path) -> None:
    source = tmp_path / "offset.glb"
    positions = struct.pack(
        "<9f",
        100.0, 5.0, 200.0,
        102.0, 5.0, 200.0,
        100.0, 7.0, 204.0,
    )
    indices = struct.pack("<3H", 0, 1, 2)
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "materials": [{"name": "pond"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
            "buffers": [{"byteLength": len(positions) + len(indices)}],
            "bufferViews": [
                {"buffer": 0, "byteOffset": 0, "byteLength": len(positions)},
                {"buffer": 0, "byteOffset": len(positions), "byteLength": len(indices)},
            ],
            "accessors": [
                {"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"},
                {"bufferView": 1, "componentType": 5123, "count": 3, "type": "SCALAR"},
            ],
        },
        bin_chunk=positions + indices,
    ).write(source)

    output = extract_material_primitives(source, tmp_path / "pond.glb", ["pond"], recenter=True)

    gltf = read_glb(output).json
    origin = next(node for node in gltf["nodes"] if node.get("name") == "rae_tile_origin")
    assert origin["translation"] == [-101.0, -5.0, -202.0]
    assert gltf["extras"]["rae"]["tileBounds"]["min"] == [100.0, 5.0, 200.0]


def test_extract_material_primitives_can_anchor_a_layered_tile_to_land_surface(
    tmp_path: Path,
) -> None:
    source = tmp_path / "layered-water.glb"
    positions = struct.pack(
        "<12f",
        0.0, -104.0, 0.0,
        16.0, -104.0, 0.0,
        0.0, -80.0, 16.0,
        16.0, -80.0, 16.0,
    )
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0}],
            "materials": [{"name": "ocean"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
            "buffers": [{"byteLength": len(positions)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"}],
        },
        bin_chunk=positions,
    ).write(source)

    output = extract_material_primitives(
        source,
        tmp_path / "ocean.glb",
        ["ocean"],
        recenter=True,
        origin_y=-80.0,
    )

    gltf = read_glb(output).json
    origin = next(node for node in gltf["nodes"] if node.get("name") == "rae_tile_origin")
    assert origin["translation"] == [-8.0, 80.0, -8.0]
    assert gltf["extras"]["rae"]["tileBounds"]["originY"] == -80.0


def test_extract_material_primitives_recenters_world_transformed_tile(tmp_path: Path) -> None:
    source = tmp_path / "placed.glb"
    positions = struct.pack(
        "<9f",
        10.0, 2.0, 20.0,
        12.0, 2.0, 20.0,
        10.0, 4.0, 24.0,
    )
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [{"mesh": 0, "translation": [1000.0, 8.0, -500.0]}],
            "materials": [{"name": "tree"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
            "buffers": [{"byteLength": len(positions)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
        },
        bin_chunk=positions,
    ).write(source)

    output = extract_material_primitives(source, tmp_path / "tree.glb", ["tree"], recenter=True)

    gltf = read_glb(output).json
    origin = next(node for node in gltf["nodes"] if node.get("name") == "rae_tile_origin")
    assert origin["translation"] == [-1011.0, -10.0, 478.0]
    assert gltf["extras"]["rae"]["tileBounds"]["min"] == [1010.0, 10.0, -480.0]


def test_recenter_complete_glb_preserves_content_and_grounds_scene(tmp_path: Path) -> None:
    source = tmp_path / "placed-building.glb"
    positions = struct.pack(
        "<9f",
        10.0, 4.0, 20.0,
        14.0, 4.0, 20.0,
        10.0, 12.0, 26.0,
    )
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "scene": 0,
            "scenes": [{"nodes": [0]}],
            "nodes": [
                {"mesh": 0, "skin": 0, "children": [1]},
                {"name": "root_bone"},
            ],
            "skins": [{"name": "building_skin", "joints": [1]}],
            "materials": [{"name": "wood"}],
            "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "material": 0}]}],
            "buffers": [{"byteLength": len(positions)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(positions)}],
            "accessors": [{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}],
            "animations": [{"name": "windmill"}],
        },
        bin_chunk=positions,
    ).write(source)

    output = recenter_glb_geometry(source, tmp_path / "grounded.glb")

    gltf = read_glb(output).json
    origin = next(node for node in gltf["nodes"] if node.get("name") == "rae_tile_origin")
    assert origin["translation"] == [-12.0, -4.0, -23.0]
    assert gltf["scenes"][0]["nodes"] == [2]
    assert gltf["nodes"][0]["skin"] == 0
    assert gltf["skins"] == [{"name": "building_skin", "joints": [1]}]
    assert gltf["animations"] == [{"name": "windmill"}]
    assert gltf["extras"]["rae"]["tileBounds"] == {
        "min": [10.0, 4.0, 20.0],
        "max": [14.0, 12.0, 26.0],
        "originTranslation": [-12.0, -4.0, -23.0],
    }


def test_tile_extractor_preview_renders_the_isolated_selection(tmp_path: Path) -> None:
    source = tmp_path / "box.glb"
    trimesh.creation.box().export(source)

    png = render_preview_materials_png(
        source_glb=source,
        selected_materials=["geometry_0"],
        mesh_labels=["geometry_0"],
        mesh_texture_paths=[None],
        texture_by_name={},
        fallback_paths=[],
        material_to_texture={},
        texture_bind_order=[],
        width=160,
        height=120,
    )

    assert png and png.startswith(b"\x89PNG\r\n\x1a\n")


def test_tile_export_preserves_uv_motion_without_baking_frames(tmp_path: Path) -> None:
    image = Image.new("RGBA", (2, 1))
    image.putdata([(255, 0, 0, 255), (0, 0, 255, 255)])
    payload = io.BytesIO()
    image.save(payload, format="PNG")
    png = payload.getvalue()
    model = tmp_path / "water.glb"
    GlbData(
        json={
            "asset": {"version": "2.0"},
            "buffers": [{"byteLength": len(png)}],
            "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": len(png)}],
            "images": [{"bufferView": 0, "mimeType": "image/png"}],
            "textures": [{"source": 0}],
            "materials": [{"name": "water", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
            "extras": {"rae": {"mapMaterialMotion": {
                "frameRate": 20,
                "defaultClip": "water_scroll",
                "clips": [{
                    "id": "water_scroll",
                    "frameCount": 2,
                    "loop": True,
                    "tracks": [{"material": "water", "frameOffsets": [[0, 0], [0.5, 0]]}],
                }],
            }}},
        },
        bin_chunk=png,
    ).write(model)
    staging = tmp_path / "bundle"

    animations = _material_motion_animations(model, staging)

    assert animations[0]["material"] == "water"
    assert animations[0]["frameDurationMs"] == 50
    assert animations[0]["type"] == "materialMotion"
    assert animations[0]["frameCount"] == 2
    assert animations[0]["offsets"] == [[0.0, 0.0], [0.5, 0.0]]
    assert not list(staging.rglob("*.png"))


def test_tile_motion_bake_honors_clamp_sampler() -> None:
    image = Image.new("RGBA", (2, 1))
    red = (255, 0, 0, 255)
    blue = (0, 0, 255, 255)
    image.putdata([red, blue])

    shifted = _motion_frame_image(
        {"frameOffsets": [[0.5, 0.0]]},
        image,
        0,
        wrap_s=_CLAMP_TO_EDGE,
    )

    assert [shifted.getpixel((x, 0)) for x in range(2)] == [blue, blue]
