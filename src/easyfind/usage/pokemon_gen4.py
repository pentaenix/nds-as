"""Pokémon Generation IV (DP/PT/HGSS) map usage extraction."""
from __future__ import annotations

from ...core.mapping import GameMapping
from ...platforms.nds.narc import NarcArchive
from ...scanner import Asset
from ..models import EasyFindAssetTag, EasyFindDocument, EasyFindLocation
from .helpers import (
    find_asset_by_archive_path,
    index_build_model_nodes,
    narc_child_index_from_path,
    place_name_overlay,
    usage_archives_for_mapping,
)
from .land_data import parse_land_data_building_model_indices
from .mapname import parse_mapname_bin
from .place_names import display_map_name, region_group_for_code
from .types import UsageExtractionResult


def _location_id_for_map(map_index: int) -> str:
    return f"map:{map_index:03d}"


def looks_like_narc_data(data: bytes) -> bool:
    return len(data) >= 4 and data[:4] == b"NARC"


def _land_data_map_files(assets: list[Asset], land_data_path: str) -> list[tuple[int, bytes]]:
    """Return (land_index, payload) from a land_data NARC or its scanned children."""
    archive_key = land_data_path.casefold().strip("/")
    parent = find_asset_by_archive_path(assets, land_data_path)
    if parent is not None and parent.data and looks_like_narc_data(parent.data):
        try:
            narc = NarcArchive(parent.data, parent.virtual_path)
            return [(index, entry.data) for index, entry in enumerate(narc.iter_files())]
        except Exception:
            pass

    children: list[tuple[int, bytes]] = []
    for asset in assets:
        path = asset.virtual_path.replace("\\", "/").casefold()
        if archive_key not in path:
            continue
        if path.rstrip("/") == archive_key:
            continue
        idx = narc_child_index_from_path(asset.virtual_path)
        if idx is None:
            continue
        children.append((idx, asset.data))
    children.sort(key=lambda item: item[0])
    return children


def extract_pokemon_gen4_usage(
    document: EasyFindDocument,
    assets: list[Asset],
    mapping: GameMapping | None,
) -> UsageExtractionResult:
    archives = usage_archives_for_mapping(mapping)
    overlay = place_name_overlay(mapping)
    map_names_path = archives.get("mapNames", "/fielddata/maptable/mapname.bin")
    land_data_path = archives.get(
        "landData",
        "/fielddata/land_data/land_data_release.narc",
    )
    build_models_path = archives.get(
        "buildModels",
        "/fielddata/build_model/build_model.narc",
    )

    mapname_asset = find_asset_by_archive_path(assets, map_names_path)
    if mapname_asset is None and map_names_path.endswith("mapname.bin"):
        mapname_asset = find_asset_by_archive_path(assets, "mapname.bin")

    raw_names: list[str] = []
    if mapname_asset is not None:
        raw_names = parse_mapname_bin(mapname_asset.data)

    locations: list[EasyFindLocation] = []
    location_by_id: dict[str, EasyFindLocation] = {}
    for map_index, raw_name in enumerate(raw_names):
        location_id = _location_id_for_map(map_index)
        name = display_map_name(
            map_index=map_index,
            raw_name=raw_name,
            overlay=overlay,
        )
        loc = EasyFindLocation(
            location_id=location_id,
            name=name,
            group=region_group_for_code(raw_name),
            kind="map",
            order=map_index,
            color=None,
            aliases=[raw_name] if raw_name else [],
            metadata={
                "map_index": map_index,
                "raw_name": raw_name,
                "display_source": "overlay" if raw_name in overlay else "code",
            },
        )
        locations.append(loc)
        location_by_id[location_id] = loc

    model_nodes = index_build_model_nodes(document, archive_path=build_models_path)
    tags: list[EasyFindAssetTag] = []
    tagged_pairs: set[tuple[str, str]] = set()

    def add_tag(node_id: str, location_id: str, *, model_index: int, confidence: str) -> None:
        key = (node_id, location_id)
        if key in tagged_pairs:
            return
        tagged_pairs.add(key)
        tags.append(
            EasyFindAssetTag(
                node_id=node_id,
                location_id=location_id,
                tags=[],
                favorite=False,
                note_id=None,
                metadata={
                    "role": "building_model",
                    "model_index": model_index,
                    "confidence": confidence,
                },
            )
        )

    for land_index, payload in _land_data_map_files(assets, land_data_path):
        if land_index >= len(raw_names) and raw_names:
            location_id = f"map:land:{land_index:03d}"
            if location_id not in location_by_id:
                loc = EasyFindLocation(
                    location_id=location_id,
                    name=f"Land map #{land_index}",
                    group="Unknown",
                    kind="map",
                    order=100000 + land_index,
                    color=None,
                    aliases=[],
                    metadata={"land_index": land_index, "raw_name": ""},
                )
                locations.append(loc)
                location_by_id[location_id] = loc
        else:
            location_id = _location_id_for_map(land_index) if raw_names else f"map:land:{land_index:03d}"
            if location_id not in location_by_id and not raw_names:
                loc = EasyFindLocation(
                    location_id=location_id,
                    name=f"Map #{land_index}",
                    group="Unknown",
                    kind="map",
                    order=land_index,
                    color=None,
                    aliases=[],
                    metadata={"land_index": land_index, "raw_name": ""},
                )
                locations.append(loc)
                location_by_id[location_id] = loc

        model_indices = parse_land_data_building_model_indices(payload)
        for model_index in model_indices:
            node_id = model_nodes.get(model_index)
            if node_id is None:
                continue
            if location_id not in location_by_id:
                continue
            add_tag(
                node_id,
                location_id,
                model_index=model_index,
                confidence="community-known",
            )

    return UsageExtractionResult(locations=locations, tags=tags)
