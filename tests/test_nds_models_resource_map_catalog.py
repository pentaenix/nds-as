from __future__ import annotations

import csv
from types import SimpleNamespace

import pytest

from rae.platforms.nds.export_module.models_resource_map_catalog import (
    MapCell,
    MapScene,
    MapSubmission,
    _combined_area_label,
    _components,
    _event_warps,
    _standalone_interior_title,
    apply_review,
)
from rae.platforms.nds.export_module.models_resource_interior_names import (
    is_white2_variant,
    suggest_interior_name,
)
from rae.platforms.nds.export_module.models_resource_maps import (
    _deduplicate_dae_textures,
    _missing_dae_textures,
    _scene_cell_positions,
)
from rae.platforms.nds.map_objects import _implicit_gate_display_placements

pytestmark = pytest.mark.nds


def test_components_join_orthogonal_cells_but_not_diagonals() -> None:
    cells = [
        MapCell(1, 0, 0, 0, 0, 1),
        MapCell(2, 0, 0, 1, 0, 1),
        MapCell(3, 0, 0, 2, 1, 1),
    ]
    components = _components(cells)
    assert [len(item) for item in components] == [2, 1]


def test_matrix_rows_advance_toward_positive_gltf_z() -> None:
    scene = MapScene("field", (
        MapCell(1, 0, 0, 0, 0, 1),
        MapCell(2, 0, 0, 0, 1, 1),
    ))
    positions = _scene_cell_positions(scene)
    assert positions[1] == (0.0, 0.0)
    assert positions[2] == (0.0, 512.0)


def test_explicit_portal_aligned_offsets_override_matrix_coordinates() -> None:
    scene = MapScene("cave", (
        MapCell(1, 10, 1, 4, 7, 1, offset_x=-512.0, offset_z=-416.0),
    ))
    assert _scene_cell_positions(scene)[1] == (-512.0, -416.0)


def test_cave_warp_records_preserve_destination_and_transition() -> None:
    import struct

    event = bytearray(28)
    event[6] = 1
    struct.pack_into("<HHH2xhhh", event, 8, 303, 2, 1026, 248, 0, 184)
    assert _event_warps(bytes(event)) == ((303, 2, 1026, 248, 0, 184),)
    assert _combined_area_label(["Area 2", "Area 3"]) == "Areas 2-3"


def test_review_can_rename_skip_and_reclassify(tmp_path) -> None:
    cell = MapCell(1, 0, 0, 0, 0, 1)
    submissions = [
        MapSubmission("one", "Town Interior", "Interior Maps", "Town", "review", "", (MapScene("a", (cell,)),)),
        MapSubmission("two", "Route 4", "Maps", "Route 4", "official", "", (MapScene("b", (cell,)),)),
    ]
    review = tmp_path / "review.csv"
    with review.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=("include", "key", "section", "title", "notes"))
        writer.writeheader()
        writer.writerow({"include": "yes", "key": "one", "section": "Interior Maps", "title": "Town Gym", "notes": "named"})
        writer.writerow({"include": "no", "key": "two", "section": "Maps", "title": "Route 4", "notes": ""})
    selected = apply_review(submissions, review)
    assert [item.title for item in selected] == ["Town Gym"]
    assert selected[0].notes == "named"


def test_internal_names_identify_castelia_facilities() -> None:
    main = suggest_interior_name("Castelia City", ("m_gym0301_00_00",), 1)
    top = suggest_interior_name("Castelia City", ("m_gym0302_00_00",), 2)
    office = suggest_interior_name("Castelia City", ("m_live01_00_00",), 3)
    assert (main.package, main.scene) == ("Castelia Gym", "Main Room")
    assert (top.package, top.scene) == ("Castelia Gym", "Top Floor")
    assert office.package == "Game Freak HQ"


def test_loading_zone_rooms_receive_standalone_submission_titles() -> None:
    assert _standalone_interior_title("Castelia Gym", "Main Room") == "Castelia Gym"
    assert _standalone_interior_title("Castelia Gym", "Top Floor") == "Castelia Gym Top Floor"
    assert _standalone_interior_title("Big Stadium", "Baseball Field") == "Big Stadium Baseball Field"


def test_liberty_garden_lighthouse_and_basement_are_distinct() -> None:
    lighthouse = suggest_interior_name("Liberty Garden", ("m_d12h01_00_00",), 1)
    basement = suggest_interior_name("Liberty Garden", ("m_d12h02_00_00",), 2)
    assert lighthouse.package == "Liberty Garden Lighthouse"
    assert basement.package == "Liberty Garden Basement"


def test_white2_only_terrain_is_excluded_from_black2_catalog() -> None:
    assert is_white2_variant(("white2404_00_00",))
    assert not is_white2_variant(("m_dun2404_00_00",))


def test_dae_texture_deduplication_rewrites_url_encoded_names(tmp_path) -> None:
    canonical = tmp_path / "Arena - Entrance_texture_0001.png"
    duplicate = tmp_path / "Arena - Field_texture_0001.png"
    canonical.write_bytes(b"same-png")
    duplicate.write_bytes(b"same-png")
    dae = tmp_path / "Arena - Field.dae"
    dae.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_images><image><init_from>Arena%20-%20Field_texture_0001.png</init_from></image></library_images>
</COLLADA>
""",
        encoding="utf-8",
    )

    _deduplicate_dae_textures(tmp_path)

    assert not duplicate.exists()
    assert "Arena%20-%20Entrance_texture_0001.png" in dae.read_text(encoding="utf-8")
    assert _missing_dae_textures(tmp_path) == []


def test_dae_texture_deduplication_handles_lowercase_unicode_escapes(tmp_path) -> None:
    canonical = tmp_path / "Pokémon Center_texture_0001.png"
    duplicate = tmp_path / "Pokémon Center_texture_0002.png"
    canonical.write_bytes(b"same-png")
    duplicate.write_bytes(b"same-png")
    dae = tmp_path / "Pokémon Center.dae"
    dae.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_images><image><init_from>Pok%c3%a9mon%20Center_texture_0002.png</init_from></image></library_images>
</COLLADA>
""",
        encoding="utf-8",
    )

    _deduplicate_dae_textures(tmp_path)

    assert not duplicate.exists()
    assert "Pok%C3%A9mon%20Center_texture_0001.png" in dae.read_text(encoding="utf-8")
    assert _missing_dae_textures(tmp_path) == []


def test_missing_dae_texture_audit_decodes_collada_urls(tmp_path) -> None:
    dae = tmp_path / "Map.dae"
    dae.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema">
  <library_images><image><init_from>Missing%20Texture.png</init_from></image></library_images>
</COLLADA>
""",
        encoding="utf-8",
    )

    assert _missing_dae_textures(tmp_path) == ["Map.dae: Missing Texture.png"]


def test_standard_gate_display_is_fitted_to_the_wall_recess(tmp_path, monkeypatch) -> None:
    import trimesh

    panel = SimpleNamespace(
        bounds=((-192.0, -2.0, -232.0), (-80.0, 88.0, -216.0)),
        visual=SimpleNamespace(material=SimpleNamespace(name="gate_kabe03")),
    )
    scene = SimpleNamespace(
        geometry={"panel": panel},
        bounds=((-264.0, -10.0, -248.0), (-40.0, 88.0, -64.0)),
    )
    monkeypatch.setattr(trimesh, "load", lambda *_args, **_kwargs: scene)
    objects = SimpleNamespace(
        models={96: SimpleNamespace(index=96, name="gelboard01")},
        placements=(),
    )

    placements = _implicit_gate_display_placements(objects, tmp_path / "gate.glb")

    assert len(placements) == 1
    assert placements[0].model_index == 96
    assert placements[0].x == pytest.approx(-136.0)
    assert placements[0].y == pytest.approx(2.0)
    assert placements[0].z == pytest.approx(216.0)
