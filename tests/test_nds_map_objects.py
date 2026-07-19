from __future__ import annotations

import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from rae.core.assets import Asset
from rae.platforms.nds.export_module.service import export_viewport_matched_glb
from rae.platforms.nds.map_objects import (
    Gen5AreaData,
    _matching_season_areas,
    _matrix_zone_for_map,
    _variant_name_parts,
    embedded_model_animation_resources,
    map_file_index,
    parse_ab_building_pack,
    parse_area_data,
    parse_gen5_map_container,
    parse_map_placements,
)
from rae.platforms.nds.inspector import NdsMapObjectsWidget


pytestmark = pytest.mark.nds


def test_map_file_index_accepts_carved_gen5_map_path() -> None:
    assert map_file_index("a/0/0/8/file_0012.bin#carved_0x14.nsbmd") == 12
    assert map_file_index("a/0/1/4/file_0012.bin") is None


def test_parse_map_placements_decodes_fixed_point_model_and_rotation() -> None:
    data = bytearray(48)
    data[:2] = b"WB"
    struct.pack_into("<H", data, 2, 3)
    struct.pack_into("<IIII", data, 4, 20, 24, 28, 48)
    data[20:24] = b"BMD0"
    struct.pack_into("<I", data, 28, 1)
    struct.pack_into("<iii", data, 32, -72 * 4096, 16 * 4096, 56 * 4096)
    struct.pack_into("<H", data, 44, 32768)
    data[46:48] = b"\x01\x99"

    placement = parse_map_placements(bytes(data))[0]

    assert (placement.x, placement.y, placement.z) == (-72.0, 16.0, 56.0)
    assert placement.model_index == 409
    assert placement.rotation_degrees == 180


def test_gc_map_container_uses_fourth_section_for_actors() -> None:
    data = bytearray(52)
    data[:2] = b"GC"
    struct.pack_into("<H", data, 2, 4)
    struct.pack_into("<IIIII", data, 4, 24, 28, 32, 36, 52)
    data[24:28] = b"BMD0"
    struct.pack_into("<I", data, 36, 0)

    container = parse_gen5_map_container(bytes(data))

    assert container.terrain_start == 24
    assert container.terrain_end == 28
    assert container.actor_start == 36


def test_parse_area_data_reads_building_pack_and_map_texture() -> None:
    first = b"\x00" * 10
    second = struct.pack("<HHBBBBBB", 7, 11, 2, 3, 1, 140, 4, 1)

    area = parse_area_data(first + second, 1)

    assert area.building_pack == 7
    assert area.map_texture == 11
    assert area.translate_animation == 2
    assert area.sequential_animation == 3
    assert area.is_outside
    assert area.ambient_light == 140
    assert area.outline_profile == 4


def test_parse_ab_building_pack_pairs_metadata_with_bmd_model() -> None:
    # Two resources means one metadata record followed by one BMD0 model.
    data = b"AB" + struct.pack("<HII", 2, 12, 16) + struct.pack("<HH", 153, 4) + b"BMD0"

    models = parse_ab_building_pack(data)

    assert list(models) == [153]
    assert models[153].pack_index == 0
    assert models[153].metadata == struct.pack("<HH", 153, 4)
    assert models[153].data == b"BMD0"
    assert models[153].name == "building_153"


def test_building_metadata_exposes_embedded_prop_animation() -> None:
    animation = bytearray(20)
    animation[:6] = b"BTA0\xff\xfe"
    struct.pack_into("<I", animation, 8, len(animation))
    metadata = b"\0" * 36 + bytes(animation)

    resources = embedded_model_animation_resources(metadata)

    assert resources == (("BTA0", bytes(animation)),)


def test_headerless_matrix_falls_back_to_zone_matrix_index() -> None:
    matrix = struct.pack("<IHHI", 0, 1, 1, 397)
    zones = bytearray(48 * 2)
    struct.pack_into("<H", zones, 4, 9)
    struct.pack_into("<H", zones, 48 + 4, 0)

    assert _matrix_zone_for_map([matrix], 397, bytes(zones)) == (0, 1)


def test_seasonal_map_names_share_one_coordinate_key() -> None:
    assert _variant_name_parts("map05_06") == ("05_06", "map", 0)
    assert _variant_name_parts("winter05_06") == ("05_06", "winter", 3)
    assert _variant_name_parts("white12_04") == ("12_04", "white", 3)


def test_adjacent_area_records_form_exportable_seasons() -> None:
    records = b"".join(
        struct.pack("<HHBBBBBB", 10, 42 + slot, 38, 0, 1, 140 + slot, 0, 1)
        for slot in range(4)
    )
    spring = Gen5AreaData(
        index=0,
        building_pack=10,
        map_texture=42,
        translate_animation=38,
        sequential_animation=0,
        building_type=1,
        ambient_light=140,
        outline_profile=0,
        unknown=1,
    )

    seasons = _matching_season_areas(records, spring)

    assert [area.map_texture for area in seasons] == [42, 43, 44, 45]
    assert [area.ambient_light for area in seasons] == [140, 141, 142, 143]


def test_exact_map_preview_reasserts_after_late_generic_preview(tmp_path: Path) -> None:
    generic = tmp_path / "generic.glb"
    exact = tmp_path / "exact.glb"
    terrain = tmp_path / "terrain.glb"
    for path in (generic, exact, terrain):
        path.write_bytes(b"glTF")
    calls: list[str] = []
    widget = SimpleNamespace(
        _composition=SimpleNamespace(composed_glb=exact, terrain_glb=terrain),
        _window=SimpleNamespace(preview=SimpleNamespace(_last_path=generic)),
        _intentional_preview_path=None,
        _show_composed=lambda: calls.append("exact"),
    )

    restored = NdsMapObjectsWidget.ensure_exact_preview(widget)

    assert restored
    assert calls == ["exact"]


def test_exact_map_preview_preserves_deliberate_object_preview(tmp_path: Path) -> None:
    object_glb = (tmp_path / "object.glb").resolve()
    exact = tmp_path / "exact.glb"
    terrain = tmp_path / "terrain.glb"
    calls: list[str] = []
    widget = SimpleNamespace(
        _composition=SimpleNamespace(composed_glb=exact, terrain_glb=terrain),
        _window=SimpleNamespace(preview=SimpleNamespace(_last_path=object_glb)),
        _intentional_preview_path=object_glb,
        _show_composed=lambda: calls.append("exact"),
    )

    restored = NdsMapObjectsWidget.ensure_exact_preview(widget)

    assert not restored
    assert calls == []
    assert NdsMapObjectsWidget.ensure_exact_preview(widget, force=True)
    assert calls == ["exact"]


def test_map_worker_result_is_consumed_only_after_thread_finished() -> None:
    calls: list[tuple] = []

    class Worker:
        asset_id = "map-11"
        result = object()
        error = ""
        automatic = True
        deleted = False

        def deleteLater(self) -> None:
            self.deleted = True

    worker = Worker()
    widget = SimpleNamespace(
        _worker=worker,
        _shutting_down=False,
        _composition_finished=lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    NdsMapObjectsWidget._map_worker_finished(widget, worker)

    assert widget._worker is None
    assert worker.deleted
    assert calls == [(('map-11', worker.result, ''), {'automatic': True})]


def test_map_worker_is_joined_before_widget_shutdown() -> None:
    calls: list[str] = []

    class Worker:
        def isRunning(self) -> bool:
            return True

        def requestInterruption(self) -> None:
            calls.append("interrupt")

        def wait(self) -> None:
            calls.append("wait")

    widget = SimpleNamespace(_worker=Worker(), _shutting_down=False)

    NdsMapObjectsWidget._shutdown_map_worker(widget)

    assert calls == ["interrupt", "wait"]
    assert widget._worker is None
    assert widget._shutting_down


def test_normal_glb_export_uses_active_exact_map_with_buildings(tmp_path: Path) -> None:
    source = tmp_path / "exact.glb"
    source.write_bytes(b"exact composed map")
    asset = Asset(
        asset_id="map-65",
        virtual_path="a/0/0/8/file_0065.bin#carved.nsbmd",
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0",
        original_data=b"BMD0",
    )
    messages: list[str] = []
    composition = SimpleNamespace(
        composed_glb=source,
        objects=SimpleNamespace(map_file_index=65, variant_label="Winter"),
    )
    host = SimpleNamespace(
        nds_map_objects=SimpleNamespace(_asset_id=asset.asset_id, _composition=composition),
        _update_status=messages.append,
    )

    written = export_viewport_matched_glb(host, asset, tmp_path / "export")

    assert len(written) == 1
    assert written[0].read_bytes() == b"exact composed map"
    assert "with_buildings" in written[0].name
    assert "terrain, placed buildings, and animations" in messages[-1]
