"""Tests for EasyFind map usage extraction and place filters."""
from __future__ import annotations

import struct

from rae.core.mapping import GameMapping
from rae.easyfind import EasyFindCanvasFilters, create_easyfind_document, filter_nodes, load_node_index
from rae.easyfind.canvas_filters import FOCUS_OP_AND, focus_overview_group_by
from rae.easyfind.models import EasyFindAssetTag, EasyFindLocation
from rae.easyfind.node_index import attach_bucket_lookup
from rae.easyfind.usage.land_data import parse_land_data_building_model_indices
from rae.easyfind.usage.mapname import parse_mapname_bin
from rae.easyfind.usage.place_names import display_map_name, region_group_for_code
from rae.easyfind.usage.pokemon_gen4 import extract_pokemon_gen4_usage
from rae.easyfind.usage.registry import merge_usage_annotations
from rae.platforms.nds.scanner import Asset
from tests.easyfind_testutil import finalize_easyfind_document


def _asset(asset_id: str, path: str, magic: str, data: bytes) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind=magic,
        magic=magic,
        extension=".bin",
        data=data,
        original_data=data,
    )


def _minimal_narc(payloads: list[bytes]) -> bytes:
    gmif = b"".join(payloads)
    btaf = struct.pack("<H", len(payloads))
    offset = 0
    for payload in payloads:
        btaf += struct.pack("<II", offset, offset + len(payload))
        offset += len(payload)
    btnf = struct.pack("<IHH", 8, 0, 1) + b"\x00"

    def _section(magic: bytes, payload: bytes) -> bytes:
        size = len(payload) + 8
        part = magic + struct.pack("<I", size) + payload
        pad = (-len(part)) % 4
        return part + (b"\x00" * pad)

    body = _section(b"BTAF", btaf) + _section(b"BTNF", btnf) + _section(b"GMIF", gmif)
    return b"NARC" + struct.pack("<HHIHH", 0xFFFE, 0x0100, len(body) + 16, 16, 3) + body


def test_parse_mapname_bin():
    data = b"T20R0101".ljust(16, b"\x00") + b"R05R0201".ljust(16, b"\x00")
    names = parse_mapname_bin(data)
    assert names == ["T20R0101", "R05R0201"]


def test_region_group_for_code():
    assert region_group_for_code("T20R0101") == "Towns"
    assert region_group_for_code("R05R0201") == "Routes"
    assert region_group_for_code("") == "Unknown"


def test_display_map_name_overlay():
    name = display_map_name(
        map_index=20,
        raw_name="T20R0101",
        overlay={"T20R0101": "Eterna Forest"},
    )
    assert name == "Eterna Forest"


def test_parse_land_data_building_models():
    sizes = struct.pack("<6I", 8, 8, 0x28, 0, 0, 0)
    permissions = b"\x00" * 8
    other = b"\x00" * 8
    building = struct.pack("<I", 7) + b"\x00" * (0x28 - 4)
    data = sizes + permissions + other + building
    assert parse_land_data_building_model_indices(data) == [7]


def test_extract_pokemon_gen4_links_model_to_map():
    mapname = b"T20R0101".ljust(16, b"\x00") + b"R05R0201".ljust(16, b"\x00")
    sizes = struct.pack("<6I", 8, 8, 0x28, 0, 0, 0)
    land0 = sizes + b"\x00" * 8 + b"\x00" * 8 + struct.pack("<I", 0) + b"\x00" * (0x28 - 4)
    land1 = sizes + b"\x00" * 8 + b"\x00" * 8 + struct.pack("<I", 1) + b"\x00" * (0x28 - 4)
    land_narc = _minimal_narc([land0, land1])
    assets = [
        _asset("mapname", "fielddata/maptable/mapname.bin", "BIN", mapname),
        _asset("land0", "fielddata/land_data/land_data_release.narc/file_0000.bin", "BIN", land0),
        _asset("land1", "fielddata/land_data/land_data_release.narc/file_0001.bin", "BIN", land1),
        _asset("m0", "fielddata/build_model/build_model.narc/file_0000.bin", "BMD0", b"BMD0" + b"x" * 16),
        _asset("m1", "fielddata/build_model/build_model.narc/file_0001.bin", "BMD0", b"BMD0" + b"y" * 16),
    ]
    doc = create_easyfind_document(assets=assets, rom_path="game.nds")
    mapping = GameMapping(
        mapping_id="pokemon_dp",
        platform="nds",
        game_family="pokemon_gen4",
        coverage="test",
        notes="",
        games=[],
        archives=[],
        ui_groups=[],
        sources=[],
        search_presets=[],
        usage_profile="pokemon_gen4",
        usage_archives={
            "mapNames": "/fielddata/maptable/mapname.bin",
            "landData": "/fielddata/land_data/land_data_release.narc",
            "buildModels": "/fielddata/build_model/build_model.narc",
        },
    )
    extracted = extract_pokemon_gen4_usage(doc, assets, mapping)
    assert len(extracted.locations) >= 2
    merge_usage_annotations(doc, extracted, preserve_manual=False)
    doc = finalize_easyfind_document(attach_bucket_lookup(doc))
    index = load_node_index(doc)

    route_models = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_map_ids=frozenset({"map:001"}),
            focus_map_op=FOCUS_OP_AND,
            focus_type="model",
            focus_type_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert len(route_models) == 1
    assert route_models[0].node_id == "asset:m1"

    town_region = filter_nodes(
        doc,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_region_groups=frozenset({"Towns"}),
            focus_region_op=FOCUS_OP_AND,
        ),
        index=index,
    )
    assert any(node.node_id == "asset:m0" for node in town_region)
    assert focus_overview_group_by(
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_map_ids=frozenset({"map:000"}),
            focus_map_op=FOCUS_OP_AND,
        )
    ) == "location"
