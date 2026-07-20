"""Pokémon Gen 5 map-object discovery and preview composition.

This is intentionally an NDS platform island.  It reads the map/zone/building
archives from a Gen 5 ROM, but does not participate in the normal model renderer.
"""
from __future__ import annotations

import re
import shutil
import struct
from dataclasses import dataclass, replace
from math import cos, radians, sin
from pathlib import Path
from statistics import median
from typing import Callable

from ...core.assets import Asset

Progress = Callable[[str], None]

_MAP_FILE_RE = re.compile(r"(?:^|/)a/0/0/8/file_(\d+)\.bin", re.IGNORECASE)
_ZONE_RECORD_SIZE = 48
_AREA_RECORD_SIZE = 10
_FIXED_POINT_SCALE = 4096.0
_MODEL_ANIMATION_INFO = {
    b"BCA0": ("Skeletal animation", ".nsbca"),
    b"BTA0": ("Texture SRT animation", ".nsbta"),
    b"BTP0": ("Texture pattern animation", ".nsbtp"),
    b"BMA0": ("Material color animation", ".nsbma"),
    b"BVA0": ("Visibility animation", ".nsbva"),
    b"BPC0": ("Material animation", ".nsbpc"),
}


@dataclass(frozen=True)
class Gen5AreaData:
    index: int
    building_pack: int
    map_texture: int
    translate_animation: int
    sequential_animation: int
    building_type: int
    ambient_light: int
    outline_profile: int
    unknown: int

    @property
    def is_outside(self) -> bool:
        return self.building_type == 1


@dataclass(frozen=True)
class Gen5MapPlacement:
    index: int
    x: float
    y: float
    z: float
    model_index: int
    rotation_raw: int

    @property
    def rotation_degrees(self) -> float:
        return self.rotation_raw * 360.0 / 65536.0


@dataclass(frozen=True)
class Gen5BuildingModel:
    index: int
    pack_index: int
    name: str
    data: bytes
    metadata: bytes


@dataclass(frozen=True)
class Gen5BuildingDoor:
    model: Gen5BuildingModel
    translation: tuple[float, float, float]


@dataclass(frozen=True)
class Gen5PlacedDoor:
    """A door attached to one placed building, with its ROM provenance."""

    placement_index: int
    model: Gen5BuildingModel
    translation: tuple[float, float, float]
    source: str
    confidence: float
    destination_zone: int | None = None
    interior_family: str = ""


@dataclass(frozen=True)
class _Gen5Warp:
    destination_zone: int
    x: int
    y: int
    z: int
    width: int
    height: int


@dataclass(frozen=True)
class _DoorProfile:
    interior_family: str
    door_model_index: int
    door_model_name: str
    correction: tuple[float, float, float]
    examples: int
    confidence: float


@dataclass(frozen=True)
class Gen5MapVariant:
    """One exportable geometry/AreaData combination for a Gen 5 map."""

    key: str
    label: str
    map_file_index: int
    area_index: int
    texture_index: int


@dataclass(frozen=True)
class Gen5MapObjectSet:
    map_file_index: int
    matrix_index: int
    zone_index: int
    area: Gen5AreaData
    placements: tuple[Gen5MapPlacement, ...]
    models: dict[int, Gen5BuildingModel]
    doors: tuple[Gen5PlacedDoor, ...]
    unresolved_model_indices: tuple[int, ...]
    terrain_data: bytes
    map_texture_data: bytes
    material_animation_data: bytes | None
    additional_material_animation_data: tuple[bytes, ...]
    pattern_animation_data: bytes | None
    texture_data: bytes
    model_archive_path: str
    texture_archive_path: str
    variant_key: str
    variant_label: str
    variants: tuple[Gen5MapVariant, ...]


@dataclass(frozen=True)
class Gen5ObjectPreview:
    placement: Gen5MapPlacement
    model: Gen5BuildingModel
    glb_path: Path
    door: Gen5PlacedDoor | None = None


@dataclass(frozen=True)
class Gen5MapComposition:
    objects: Gen5MapObjectSet
    terrain_glb: Path
    composed_glb: Path
    previews: tuple[Gen5ObjectPreview, ...]


@dataclass(frozen=True)
class Gen5MapContainer:
    magic: bytes
    section_offsets: tuple[int, ...]
    declared_size: int
    terrain_start: int
    terrain_end: int
    actor_start: int
    actor_end: int


def map_file_index(virtual_path: str) -> int | None:
    match = _MAP_FILE_RE.search(str(virtual_path or ""))
    return int(match.group(1)) if match else None


def parse_gen5_map_container(data: bytes) -> Gen5MapContainer:
    """Read the variable Gen 5 map header and locate terrain/actor sections."""
    if len(data) < 16 or data[:2] not in {b"WB", b"GC", b"NG", b"DR", b"RD"}:
        raise ValueError("Selected file is not a supported Gen 5 map container")
    section_count = struct.unpack_from("<H", data, 2)[0]
    header_size = 8 + section_count * 4
    if section_count < 2 or header_size > len(data):
        raise ValueError("Gen 5 map section table is truncated")
    offsets = tuple(struct.unpack_from(f"<{section_count}I", data, 4))
    declared_size = struct.unpack_from("<I", data, 4 + section_count * 4)[0]
    if declared_size == 0:
        declared_size = len(data)
    if declared_size > len(data) or offsets != tuple(sorted(offsets)):
        raise ValueError("Gen 5 map has invalid section offsets")
    if offsets[0] < header_size or offsets[-1] >= declared_size:
        raise ValueError("Gen 5 map has invalid section offsets")

    actor_section = {b"NG": 1, b"WB": 2, b"DR": 2, b"RD": 2, b"GC": 3}[data[:2]]
    if actor_section >= section_count:
        raise ValueError("Gen 5 map does not contain its expected actor section")
    terrain_start, terrain_end = offsets[0], offsets[1]
    if terrain_start >= terrain_end or data[terrain_start : terrain_start + 4] != b"BMD0":
        raise ValueError("Gen 5 map terrain model offsets are invalid")
    actor_start = offsets[actor_section]
    if actor_start + 4 > declared_size:
        raise ValueError("Gen 5 map has an invalid placed-object section")
    return Gen5MapContainer(
        magic=data[:2],
        section_offsets=offsets,
        declared_size=declared_size,
        terrain_start=terrain_start,
        terrain_end=terrain_end,
        actor_start=actor_start,
        actor_end=declared_size,
    )


def parse_map_placements(data: bytes) -> tuple[Gen5MapPlacement, ...]:
    """Read Gen 5 map actors, preserving full model IDs and free rotation."""
    container = parse_gen5_map_container(data)
    count = struct.unpack_from("<I", data, container.actor_start)[0]
    if count > (container.actor_end - container.actor_start - 4) // 16:
        raise ValueError("Gen 5 placed-object count exceeds its section")

    placements: list[Gen5MapPlacement] = []
    for index in range(count):
        raw = data[
            container.actor_start + 4 + index * 16 :
            container.actor_start + 20 + index * 16
        ]
        x, y, z = struct.unpack_from("<iii", raw, 0)
        placements.append(
            Gen5MapPlacement(
                index=index,
                x=x / _FIXED_POINT_SCALE,
                y=y / _FIXED_POINT_SCALE,
                z=z / _FIXED_POINT_SCALE,
                rotation_raw=struct.unpack_from("<H", raw, 12)[0],
                model_index=(raw[14] << 8) | raw[15],
            )
        )
    return tuple(placements)


def parse_area_data(data: bytes, index: int) -> Gen5AreaData:
    start = index * _AREA_RECORD_SIZE
    if index < 0 or start + _AREA_RECORD_SIZE > len(data):
        raise ValueError(f"AreaData index {index} is outside the archive")
    raw = data[start : start + _AREA_RECORD_SIZE]
    return Gen5AreaData(
        index=index,
        building_pack=struct.unpack_from("<H", raw, 0)[0],
        map_texture=struct.unpack_from("<H", raw, 2)[0],
        translate_animation=raw[4],
        sequential_animation=raw[5],
        building_type=raw[6],
        ambient_light=raw[7],
        outline_profile=raw[8],
        unknown=raw[9],
    )


def parse_ab_building_pack(data: bytes) -> dict[int, Gen5BuildingModel]:
    """Split one Gen 5 ``AB`` pack into metadata/model pairs."""
    if len(data) < 8 or data[:2] != b"AB":
        raise ValueError("Building pack does not have an AB header")
    resource_count = struct.unpack_from("<H", data, 2)[0]
    if resource_count <= 0 or resource_count % 2:
        raise ValueError("Building pack has an invalid resource count")
    header_end = 4 + resource_count * 4
    if header_end > len(data):
        raise ValueError("Building pack offset table is truncated")
    offsets = list(struct.unpack_from(f"<{resource_count}I", data, 4))
    if offsets != sorted(offsets) or offsets[0] < header_end or offsets[-1] >= len(data):
        raise ValueError("Building pack has invalid resource offsets")
    ends = offsets[1:] + [len(data)]
    model_count = resource_count // 2
    models: dict[int, Gen5BuildingModel] = {}
    for pack_index in range(model_count):
        metadata = bytes(data[offsets[pack_index] : ends[pack_index]])
        if len(metadata) < 2:
            continue
        definition_id = struct.unpack_from("<H", metadata, 0)[0]
        model_start = offsets[model_count + pack_index]
        model_end = ends[model_count + pack_index]
        model_data = bytes(data[model_start:model_end])
        if model_data[:4] != b"BMD0":
            continue
        from .nitro_models import parse_nsbmd_manifest

        manifest = parse_nsbmd_manifest(model_data)
        names = list(manifest.model_names) if manifest else []
        name = names[0] if names else f"building_{definition_id:03d}"
        models[definition_id] = Gen5BuildingModel(
            index=definition_id,
            pack_index=pack_index,
            name=name,
            data=model_data,
            metadata=metadata,
        )
    return models


def embedded_model_animation_resources(metadata: bytes) -> tuple[tuple[str, bytes], ...]:
    """Extract Nitro animations bundled after a Gen 5 building definition.

    The first half of an ``AB`` pack is more than placement metadata: animated
    props append complete NSBCA/NSBTA/etc. files to their 36-byte definition.
    Treating that record as opaque is why fountains appeared static.
    """
    resources: list[tuple[str, bytes]] = []
    cursor = 0
    while cursor + 16 <= len(metadata):
        matches = [
            (metadata.find(magic, cursor), magic)
            for magic in _MODEL_ANIMATION_INFO
        ]
        matches = [(offset, magic) for offset, magic in matches if offset >= 0]
        if not matches:
            break
        offset, magic = min(matches, key=lambda item: item[0])
        if metadata[offset + 4 : offset + 6] not in {b"\xff\xfe", b"\xfe\xff"}:
            cursor = offset + 4
            continue
        size = struct.unpack_from("<I", metadata, offset + 8)[0]
        if size < 16 or offset + size > len(metadata):
            cursor = offset + 4
            continue
        resources.append((magic.decode("ascii"), bytes(metadata[offset : offset + size])))
        cursor = offset + size
    return tuple(resources)


def building_door_attachment(
    building: Gen5BuildingModel,
    models: dict[int, Gen5BuildingModel],
) -> Gen5BuildingDoor | None:
    """Return the door model and authored local offset from an AB definition."""
    if len(building.metadata) < 12:
        return None
    door_id = struct.unpack_from("<H", building.metadata, 4)[0]
    if door_id == 0xFFFF or door_id == building.index:
        return None
    door = models.get(door_id)
    if door is None:
        return None
    # The three signed shorts following the door definition id are local model
    # coordinates. Apicula's map handoff negates Nitro Z, matching the placed
    # object transform used below.
    x, y, z = struct.unpack_from("<hhh", building.metadata, 6)
    return Gen5BuildingDoor(
        model=door,
        translation=(float(x), float(y), -float(z)),
    )


def _narc_files(data: bytes, virtual_path: str) -> list[bytes]:
    from .narc import NarcArchive, looks_like_narc

    if not looks_like_narc(data):
        raise ValueError(f"{virtual_path} is not a NARC archive")
    return [item.data for item in NarcArchive(data, virtual_path).iter_files()]


_SEASON_PREFIXES = {
    "summer": (1, "Summer"),
    "autumn": (2, "Autumn"),
    "winter": (3, "Winter"),
    "white": (3, "Winter (snow geometry)"),
}
_SPECIAL_VARIANT_LABELS = {
    "freezew": "Frozen water",
    "freezeb": "Frozen bridge",
    "freezemap": "Frozen map",
}


def _terrain_model_name(map_container: bytes) -> str:
    """Return the authoritative MDL0 name stored in one map container."""
    from .nitro_models import parse_nsbmd_manifest

    container = parse_gen5_map_container(map_container)
    manifest = parse_nsbmd_manifest(
        map_container[container.terrain_start : container.terrain_end]
    )
    if manifest and manifest.model_names:
        return str(manifest.model_names[0])
    return ""


def _variant_name_parts(value: str) -> tuple[str, str, int | None]:
    """Return canonical coordinate key, prefix, and seasonal slot."""
    name = str(value or "").casefold()
    match = re.fullmatch(
        r"(freezemap|freezew|freezeb|summer|autumn|winter|white|map)(\d+_\d+)",
        name,
    )
    if not match:
        return name, "", None
    prefix, coordinate = match.groups()
    slot = _SEASON_PREFIXES.get(prefix, (None, ""))[0]
    if prefix == "map":
        slot = 0
    return coordinate, prefix, slot


def _matrix_zone_references(
    matrix_files: list[bytes],
    zone_data: bytes,
) -> dict[int, tuple[int, int]]:
    """Index map IDs that have an authoritative or matrix-derived zone."""
    references: dict[int, tuple[int, int]] = {}
    headerless: list[tuple[int, int]] = []
    for matrix_index, data in enumerate(matrix_files):
        if len(data) < 8:
            continue
        include_headers, width, height = struct.unpack_from("<IHH", data, 0)
        count = width * height
        maps_end = 8 + count * 4
        headers_end = maps_end + count * 4
        if count <= 0 or maps_end > len(data):
            continue
        map_ids = struct.unpack_from(f"<{count}I", data, 8)
        for cell, map_id in enumerate(map_ids):
            if map_id == 0xFFFFFFFF:
                continue
            if include_headers and headers_end <= len(data):
                zone_index = struct.unpack_from("<I", data, maps_end + cell * 4)[0]
                if zone_index != 0xFFFFFFFF:
                    references.setdefault(map_id, (matrix_index, zone_index))
                    continue
            headerless.append((map_id, matrix_index))
    zone_count = len(zone_data) // _ZONE_RECORD_SIZE
    zones_by_matrix: dict[int, int] = {}
    for zone_index in range(zone_count):
        matrix_index = struct.unpack_from(
            "<H", zone_data, zone_index * _ZONE_RECORD_SIZE + 4
        )[0]
        zones_by_matrix.setdefault(matrix_index, zone_index)
    for map_id, matrix_index in headerless:
        if matrix_index in zones_by_matrix:
            references.setdefault(map_id, (matrix_index, zones_by_matrix[matrix_index]))
    return references


def _matrix_map_locations(
    matrix_files: list[bytes],
    zone_data: bytes,
) -> dict[int, tuple[int, int, int, int]]:
    """Return map -> (matrix, zone, cell x, cell y) for live matrix cells."""
    locations: dict[int, tuple[int, int, int, int]] = {}
    for map_id, location in _matrix_all_map_locations(matrix_files, zone_data):
        locations.setdefault(map_id, location)
    return locations


def _matrix_all_map_locations(
    matrix_files: list[bytes],
    zone_data: bytes,
) -> tuple[tuple[int, tuple[int, int, int, int]], ...]:
    """Return every live matrix occurrence, including reused map geometry."""
    zone_by_matrix: dict[int, int] = {}
    for zone_index in range(len(zone_data) // _ZONE_RECORD_SIZE):
        matrix_index = struct.unpack_from(
            "<H", zone_data, zone_index * _ZONE_RECORD_SIZE + 4
        )[0]
        zone_by_matrix.setdefault(matrix_index, zone_index)
    locations: list[tuple[int, tuple[int, int, int, int]]] = []
    for matrix_index, data in enumerate(matrix_files):
        if len(data) < 8:
            continue
        include_headers, width, height = struct.unpack_from("<IHH", data, 0)
        count = width * height
        maps_end = 8 + count * 4
        headers_end = maps_end + count * 4
        if count <= 0 or maps_end > len(data):
            continue
        map_ids = struct.unpack_from(f"<{count}I", data, 8)
        for cell, map_id in enumerate(map_ids):
            if map_id == 0xFFFFFFFF:
                continue
            zone_index = zone_by_matrix.get(matrix_index)
            if include_headers and headers_end <= len(data):
                candidate = struct.unpack_from("<I", data, maps_end + cell * 4)[0]
                if candidate != 0xFFFFFFFF:
                    zone_index = candidate
            if zone_index is not None:
                locations.append(
                    (
                        map_id,
                        (matrix_index, zone_index, cell % width, cell // width),
                    )
                )
    return tuple(locations)


def _parse_gen5_warps(data: bytes) -> tuple[_Gen5Warp, ...]:
    """Read the fixed-size warp section of a Gen 5 event file."""
    if len(data) < 8:
        return ()
    furniture_count = data[4]
    actor_count = data[5]
    warp_count = data[6]
    # Event sections are ordered, rather than packed backward from EOF:
    # 8-byte header, 20-byte furniture records, 36-byte actor records, then
    # 20-byte warps. Trigger records that follow are variable across versions.
    start = 8 + furniture_count * 20 + actor_count * 36
    if start < 8 or start + warp_count * 20 > len(data):
        return ()
    warps: list[_Gen5Warp] = []
    for index in range(warp_count):
        offset = start + index * 20
        destination_zone = struct.unpack_from("<H", data, offset)[0]
        x, y, z, width, height = struct.unpack_from("<hhhhh", data, offset + 8)
        warps.append(
            _Gen5Warp(
                destination_zone=destination_zone,
                x=x,
                y=y,
                z=z,
                width=width,
                height=height,
            )
        )
    return tuple(warps)


def _zone_event_index(zone_data: bytes, zone_index: int) -> int | None:
    offset = zone_index * _ZONE_RECORD_SIZE
    if offset < 0 or offset + _ZONE_RECORD_SIZE > len(zone_data):
        return None
    # Field 11 in the BW/B2W2 ZoneData record selects a/1/2/5.
    return struct.unpack_from("<H", zone_data, offset + 22)[0]


def _placement_local_point(
    placement: Gen5MapPlacement,
    world_x: float,
    world_y: float,
    world_z: float,
) -> tuple[float, float, float]:
    """Transform a GLB-world point into the placement's local coordinates."""
    angle = radians(placement.rotation_degrees)
    dx = world_x - placement.x
    dz = world_z - (-placement.z)
    return (
        cos(angle) * dx - sin(angle) * dz,
        world_y - placement.y,
        sin(angle) * dx + cos(angle) * dz,
    )


def _placed_door_world_point(
    placement: Gen5MapPlacement,
    translation: tuple[float, float, float],
) -> tuple[float, float, float]:
    angle = radians(placement.rotation_degrees)
    x, y, z = translation
    return (
        placement.x + cos(angle) * x + sin(angle) * z,
        placement.y + y,
        -placement.z - sin(angle) * x + cos(angle) * z,
    )


def _matrix_zone_for_map(
    matrix_files: list[bytes],
    map_index: int,
    zone_data: bytes,
) -> tuple[int, int]:
    reference = _matrix_zone_references(matrix_files, zone_data).get(map_index)
    if reference is not None:
        return reference
    raise ValueError(f"No zone header references map file {map_index}")


def _season_slot(area: Gen5AreaData) -> int | None:
    if 140 <= area.ambient_light <= 143:
        return area.ambient_light - 140
    if 0 <= area.ambient_light <= 3:
        return area.ambient_light
    return None


def _matching_season_areas(area_data: bytes, area: Gen5AreaData) -> tuple[Gen5AreaData, ...]:
    """Find the four adjacent AreaData records used by Gen 5 seasons."""
    slot = _season_slot(area)
    if slot is None:
        return (area,)
    start = area.index - slot
    if start < 0:
        return (area,)
    candidates: list[Gen5AreaData] = []
    try:
        candidates = [parse_area_data(area_data, start + index) for index in range(4)]
    except ValueError:
        return (area,)
    signature = (
        area.building_pack,
        area.translate_animation,
        area.sequential_animation,
        area.building_type,
        area.outline_profile,
    )
    if any(
        (
            candidate.building_pack,
            candidate.translate_animation,
            candidate.sequential_animation,
            candidate.building_type,
            candidate.outline_profile,
        )
        != signature
        for candidate in candidates
    ):
        return (area,)
    if [_season_slot(candidate) for candidate in candidates] != [0, 1, 2, 3]:
        return (area,)
    return tuple(candidates)


def _requested_texture_names(terrain_data: bytes) -> set[str]:
    from .nitro_models import parse_nsbmd_manifest

    manifest = parse_nsbmd_manifest(terrain_data)
    requested: set[str] = set()
    for binding in manifest.materials if manifest else []:
        for name in (binding.material_name, binding.texture_name):
            if not name:
                continue
            key = str(name).casefold()
            requested.add(key)
            requested.add(re.sub(r"_lm\d+$", "", key))
    return requested


def _fallback_area_for_map(
    *,
    terrain_data: bytes,
    placements: tuple[Gen5MapPlacement, ...],
    area_data: bytes,
    map_textures: list[bytes],
    outside_model_packs: list[bytes],
) -> Gen5AreaData | None:
    """Resolve unused/showcase maps by exact texture names and model IDs."""
    from .nitro_names import extract_nitro_names

    requested = _requested_texture_names(terrain_data)
    if not requested:
        return None
    texture_scores: dict[int, int] = {}
    for texture_index, payload in enumerate(map_textures):
        names = {name.casefold() for name in extract_nitro_names(payload)}
        texture_scores[texture_index] = len(requested & names)
    best_texture_score = max(texture_scores.values(), default=0)
    if best_texture_score <= 0:
        return None

    placement_ids = {item.model_index for item in placements}
    scored: list[tuple[int, int, int, Gen5AreaData]] = []
    model_cache: dict[int, set[int]] = {}
    for area_index in range(len(area_data) // _AREA_RECORD_SIZE):
        candidate = parse_area_data(area_data, area_index)
        texture_score = texture_scores.get(candidate.map_texture, 0)
        if texture_score != best_texture_score or not candidate.is_outside:
            continue
        definitions: set[int] = set()
        if candidate.building_pack < len(outside_model_packs):
            definitions = model_cache.get(candidate.building_pack, set())
            if candidate.building_pack not in model_cache:
                try:
                    definitions = set(parse_ab_building_pack(outside_model_packs[candidate.building_pack]))
                except ValueError:
                    definitions = set()
                model_cache[candidate.building_pack] = definitions
        resolved = len(placement_ids & definitions)
        missing = len(placement_ids - definitions)
        slot = _season_slot(candidate)
        scored.append((resolved, -missing, -(slot if slot is not None else 99), candidate))
    if not scored:
        return None
    return max(scored, key=lambda item: (item[0], item[1], item[2], -item[3].index))[3]


def _map_variants(
    maps: list[bytes],
    selected_map: int,
    area_data: bytes,
    area: Gen5AreaData,
) -> tuple[Gen5MapVariant, ...]:
    selected_name = _terrain_model_name(maps[selected_map])
    canonical, _prefix, _slot = _variant_name_parts(selected_name)
    geometry: dict[str, int] = {}
    for index, payload in enumerate(maps):
        try:
            name = _terrain_model_name(payload)
        except ValueError:
            continue
        key, prefix, _candidate_slot = _variant_name_parts(name)
        if key == canonical and prefix:
            geometry.setdefault(prefix, index)

    seasons = _matching_season_areas(area_data, area)
    variants: list[Gen5MapVariant] = []
    if len(seasons) == 4:
        seasonal_geometry = {
            0: geometry.get("map", selected_map),
            1: geometry.get("summer", geometry.get("map", selected_map)),
            2: geometry.get("autumn", geometry.get("map", selected_map)),
            3: geometry.get(
                "winter",
                geometry.get("white", geometry.get("map", selected_map)),
            ),
        }
        for slot, label in enumerate(("Spring", "Summer", "Autumn", "Winter")):
            candidate = seasons[slot]
            map_index = seasonal_geometry[slot]
            variants.append(
                Gen5MapVariant(
                    key=f"{map_index}:{candidate.index}",
                    label=label,
                    map_file_index=map_index,
                    area_index=candidate.index,
                    texture_index=candidate.map_texture,
                )
            )
    else:
        variants.append(
            Gen5MapVariant(
                key=f"{selected_map}:{area.index}",
                label="Default",
                map_file_index=selected_map,
                area_index=area.index,
                texture_index=area.map_texture,
            )
        )
    known = {variant.key for variant in variants}
    for prefix, label in _SPECIAL_VARIANT_LABELS.items():
        map_index = geometry.get(prefix)
        if map_index is None:
            continue
        key = f"{map_index}:{area.index}"
        if key not in known:
            variants.append(
                Gen5MapVariant(
                    key=key,
                    label=label,
                    map_file_index=map_index,
                    area_index=area.index,
                    texture_index=area.map_texture,
                )
            )
            known.add(key)
    return tuple(variants)


def _gen5_building_archives(
    rom_files: dict[str, bytes],
    *,
    outside: bool,
) -> tuple[str, str, list[bytes], list[bytes]]:
    """Select the matching BW or B2W2 model/texture archive pair.

    The two Gen 5 releases use the same AreaData records but moved the AB
    building packs. Checking the actual AB/BTX0 members keeps this resolver
    release-independent instead of treating Black/White texture data as a
    malformed B2W2 model pack.
    """
    candidates = (
        (("a/2/2/9", "a/1/7/6"), ("a/2/2/5", "a/1/7/4"))
        if outside
        else (("a/2/3/0", "a/1/7/7"), ("a/2/2/6", "a/1/7/5"))
    )
    for model_path, texture_path in candidates:
        if model_path not in rom_files or texture_path not in rom_files:
            continue
        model_packs = _narc_files(rom_files[model_path], model_path)
        texture_packs = _narc_files(rom_files[texture_path], texture_path)
        if any(payload[:2] == b"AB" for payload in model_packs) and any(
            payload[:4] == b"BTX0" for payload in texture_packs
        ):
            return model_path, texture_path, model_packs, texture_packs
    kind = "outside" if outside else "inside"
    raise ValueError(f"ROM is missing a recognized Gen 5 {kind} building archive pair")


def _gen5_outside_model_packs(rom_files: dict[str, bytes]) -> list[bytes]:
    try:
        return _gen5_building_archives(rom_files, outside=True)[2]
    except ValueError:
        return []


_DOOR_PROFILE_CACHE: dict[str, dict[str, _DoorProfile]] = {}


def _matrix_map_ids(data: bytes) -> tuple[int, ...]:
    if len(data) < 8:
        return ()
    _headers, width, height = struct.unpack_from("<IHH", data, 0)
    count = width * height
    if count <= 0 or 8 + count * 4 > len(data):
        return ()
    return tuple(
        map_id
        for map_id in struct.unpack_from(f"<{count}I", data, 8)
        if map_id != 0xFFFFFFFF
    )


def _interior_family_for_zone(
    zone_index: int,
    zone_data: bytes,
    matrix_files: list[bytes],
    maps: list[bytes],
) -> str:
    offset = zone_index * _ZONE_RECORD_SIZE
    if offset < 0 or offset + _ZONE_RECORD_SIZE > len(zone_data):
        return ""
    matrix_index = struct.unpack_from("<H", zone_data, offset + 4)[0]
    if matrix_index >= len(matrix_files):
        return ""
    names: list[str] = []
    for map_id in _matrix_map_ids(matrix_files[matrix_index]):
        if map_id >= len(maps):
            continue
        try:
            name = _terrain_model_name(maps[map_id]).casefold()
        except ValueError:
            continue
        if name:
            names.append(name)
    return next((name for name in names if name.startswith("m_")), names[0] if names else "")


def _learn_door_profiles(
    cache_key: str,
    rom_files: dict[str, bytes],
) -> dict[str, _DoorProfile]:
    """Learn entrance-family door choices and offsets from explicit ROM pairs."""
    cached = _DOOR_PROFILE_CACHE.get(cache_key)
    if cached is not None:
        return cached
    required = ("a/0/0/8", "a/0/0/9", "a/0/1/2", "a/0/1/3", "a/1/2/5")
    if any(path not in rom_files for path in required):
        _DOOR_PROFILE_CACHE[cache_key] = {}
        return {}
    maps = _narc_files(rom_files["a/0/0/8"], "a/0/0/8")
    matrices = _narc_files(rom_files["a/0/0/9"], "a/0/0/9")
    zone_files = _narc_files(rom_files["a/0/1/2"], "a/0/1/2")
    events = _narc_files(rom_files["a/1/2/5"], "a/1/2/5")
    if not zone_files:
        _DOOR_PROFILE_CACHE[cache_key] = {}
        return {}
    zone_data = zone_files[0]
    area_data = rom_files["a/0/1/3"]
    locations = _matrix_all_map_locations(matrices, zone_data)
    try:
        _model_path, _texture_path, model_packs, _texture_packs = _gen5_building_archives(
            rom_files, outside=True
        )
    except ValueError:
        _DOOR_PROFILE_CACHE[cache_key] = {}
        return {}
    parsed_packs: dict[int, dict[int, Gen5BuildingModel]] = {}
    observations: dict[
        str,
        list[tuple[int, str, tuple[float, float, float]]],
    ] = {}
    for map_id, (_matrix, zone_index, cell_x, cell_y) in locations:
        if map_id >= len(maps):
            continue
        zone_offset = zone_index * _ZONE_RECORD_SIZE
        if zone_offset + _ZONE_RECORD_SIZE > len(zone_data):
            continue
        try:
            area_index = struct.unpack_from("<H", zone_data, zone_offset + 2)[0]
            area = parse_area_data(area_data, area_index)
            if not area.is_outside or area.building_pack >= len(model_packs):
                continue
            placements = parse_map_placements(maps[map_id])
        except ValueError:
            continue
        event_index = _zone_event_index(zone_data, zone_index)
        if event_index is None or event_index >= len(events):
            continue
        warps = [
            warp
            for warp in _parse_gen5_warps(events[event_index])
            if warp.width == 1 and warp.height == 1
        ]
        if not warps:
            continue
        pack_index = area.building_pack
        placement_ids = {placement.model_index for placement in placements}
        if placement_ids:
            scores: list[tuple[int, int, int]] = []
            for candidate_index, payload in enumerate(model_packs):
                if candidate_index not in parsed_packs:
                    try:
                        parsed = parse_ab_building_pack(payload)
                        parsed_packs[candidate_index] = _complete_referenced_door_models(
                            parsed, model_packs
                        )
                    except ValueError:
                        parsed_packs[candidate_index] = {}
                coverage = len(placement_ids & set(parsed_packs[candidate_index]))
                scores.append((coverage, int(candidate_index == area.building_pack), -candidate_index))
            if scores:
                best = max(scores)
                if best[0] > 0:
                    pack_index = -best[2]
        if pack_index not in parsed_packs:
            try:
                parsed = parse_ab_building_pack(model_packs[pack_index])
                parsed_packs[pack_index] = _complete_referenced_door_models(
                    parsed, model_packs
                )
            except ValueError:
                parsed_packs[pack_index] = {}
        models = parsed_packs[pack_index]
        center_x = cell_x * 512 + 256
        center_z = cell_y * 512 + 256
        candidates = [
            (
                warp,
                float(warp.x - center_x),
                float(warp.y),
                float(warp.z - center_z),
            )
            for warp in warps
            if abs(warp.x - center_x) <= 256 and abs(warp.z - center_z) <= 256
        ]
        used_warps: set[int] = set()
        for placement in placements:
            building = models.get(placement.model_index)
            if building is None:
                continue
            door = building_door_attachment(building, models)
            if door is None:
                continue
            door_world = _placed_door_world_point(placement, door.translation)
            nearest: tuple[float, int, _Gen5Warp, float, float, float] | None = None
            for warp_index, (warp, world_x, world_y, world_z) in enumerate(candidates):
                if warp_index in used_warps:
                    continue
                distance = ((world_x - door_world[0]) ** 2 + (world_z - door_world[2]) ** 2) ** 0.5
                candidate = (distance, warp_index, warp, world_x, world_y, world_z)
                if nearest is None or candidate[0] < nearest[0]:
                    nearest = candidate
            if nearest is None or nearest[0] > 64.0:
                continue
            _distance, warp_index, warp, world_x, world_y, world_z = nearest
            family = _interior_family_for_zone(
                warp.destination_zone, zone_data, matrices, maps
            )
            if not family:
                continue
            used_warps.add(warp_index)
            local = _placement_local_point(placement, world_x, world_y, world_z)
            correction = tuple(
                door.translation[index] - local[index] for index in range(3)
            )
            observations.setdefault(family, []).append(
                (door.model.index, door.model.name, correction)
            )
    profiles: dict[str, _DoorProfile] = {}
    for family, values in observations.items():
        counts: dict[tuple[int, str], int] = {}
        for model_index, model_name, _correction in values:
            key = (model_index, model_name)
            counts[key] = counts.get(key, 0) + 1
        (model_index, model_name), example_count = max(
            counts.items(), key=lambda item: (item[1], item[0][1])
        )
        matching = [
            correction
            for candidate_index, candidate_name, correction in values
            if (candidate_index, candidate_name) == (model_index, model_name)
        ]
        dominance = example_count / len(values)
        # One-off pairings are useful diagnostics but too weak to synthesize a
        # missing AB relationship. Requiring two unanimous ROM examples keeps
        # inferred doors limited to genuinely reusable interior families.
        if example_count < 2 or dominance < 1.0:
            continue
        profiles[family] = _DoorProfile(
            interior_family=family,
            door_model_index=model_index,
            door_model_name=model_name,
            correction=tuple(median(axis) for axis in zip(*matching)),
            examples=example_count,
            confidence=min(0.99, 0.84 + example_count * 0.03),
        )
    corrections_by_door: dict[tuple[int, str], list[tuple[float, float, float]]] = {}
    for values in observations.values():
        for model_index, model_name, correction in values:
            corrections_by_door.setdefault((model_index, model_name), []).append(correction)
    archetype_values: dict[str, list[tuple[int, str]]] = {}
    seen_definitions: set[tuple[int, int, str]] = set()
    for pack_index, pack_models in parsed_packs.items():
        for building in pack_models.values():
            definition_key = (pack_index, building.index, building.name)
            if definition_key in seen_definitions:
                continue
            seen_definitions.add(definition_key)
            archetype = _building_archetype(building.name)
            if not archetype:
                continue
            door = building_door_attachment(building, pack_models)
            if door is not None:
                archetype_values.setdefault(archetype, []).append(
                    (door.model.index, door.model.name)
                )
    for archetype, values in archetype_values.items():
        counts: dict[tuple[int, str], int] = {}
        for key in values:
            counts[key] = counts.get(key, 0) + 1
        (model_index, model_name), example_count = max(
            counts.items(), key=lambda item: (item[1], item[0][1])
        )
        dominance = example_count / len(values)
        corrections = corrections_by_door.get((model_index, model_name), [])
        if example_count < 2 or dominance < 1.0 or not corrections:
            continue
        profiles[f"model:{archetype}"] = _DoorProfile(
            interior_family=f"model:{archetype}",
            door_model_index=model_index,
            door_model_name=model_name,
            correction=tuple(median(axis) for axis in zip(*corrections)),
            examples=example_count,
            confidence=min(0.95, 0.80 + example_count * 0.03),
        )
    _DOOR_PROFILE_CACHE[cache_key] = profiles
    return profiles


def _looks_like_entrance_building(name: str) -> bool:
    value = name.casefold()
    return any(
        token in value
        for token in (
            "build", "house", "shop", "center", "school", "gym", "rest",
            "labo", "cafe", "tower", "gate", "mart", "hotel", "airport",
        )
    )


def _building_archetype(name: str) -> str:
    """Reduce town-specific exterior names to a reusable building family."""
    value = re.sub(r"[^a-z0-9]", "", name.casefold())
    for token in ("building", "build", "house", "shop", "mart", "hotel"):
        offset = value.find(token)
        if offset >= 0:
            return value[offset:]
    return ""


def _complete_referenced_door_models(
    models: dict[int, Gen5BuildingModel],
    model_packs: list[bytes],
) -> dict[int, Gen5BuildingModel]:
    """Fill shared door definitions omitted from a particular AB pack.

    Black reuses a global door UID table, but a small number of area packs
    reference a door whose model pair is only stored in another AB bundle.
    Resolve only explicit metadata references and only door-named resources so
    unrelated same-UID building models can never leak between area packs.
    """
    missing: set[int] = set()
    for model in models.values():
        if len(model.metadata) < 6:
            continue
        door_id = struct.unpack_from("<H", model.metadata, 4)[0]
        if door_id not in {0xFFFF, model.index} and door_id not in models:
            missing.add(door_id)
    if not missing:
        return models
    completed = dict(models)
    for payload in model_packs:
        try:
            candidates = parse_ab_building_pack(payload)
        except ValueError:
            continue
        for model_id in tuple(missing):
            candidate = candidates.get(model_id)
            if candidate is None or "door" not in candidate.name.casefold():
                continue
            completed[model_id] = candidate
            missing.remove(model_id)
        if not missing:
            break
    return completed


def _resolve_placed_doors(
    *,
    rom_path: str | Path,
    rom_files: dict[str, bytes],
    selected_map: int,
    placements: tuple[Gen5MapPlacement, ...],
    models: dict[int, Gen5BuildingModel],
    model_packs: list[bytes],
) -> tuple[dict[int, Gen5BuildingModel], tuple[Gen5PlacedDoor, ...]]:
    """Combine explicit AB doors with conservative event-warp inference."""
    resolved_models = dict(models)
    placed_doors: list[Gen5PlacedDoor] = []
    explicit_placements: set[int] = set()
    for placement in placements:
        building = resolved_models.get(placement.model_index)
        if building is None:
            continue
        door = building_door_attachment(building, resolved_models)
        if door is None:
            continue
        explicit_placements.add(placement.index)
        placed_doors.append(
            Gen5PlacedDoor(
                placement_index=placement.index,
                model=door.model,
                translation=door.translation,
                source="ab",
                confidence=1.0,
            )
        )
    required = ("a/0/0/8", "a/0/0/9", "a/0/1/2", "a/1/2/5")
    if any(path not in rom_files for path in required):
        return resolved_models, tuple(placed_doors)
    maps = _narc_files(rom_files["a/0/0/8"], "a/0/0/8")
    matrices = _narc_files(rom_files["a/0/0/9"], "a/0/0/9")
    zone_files = _narc_files(rom_files["a/0/1/2"], "a/0/1/2")
    events = _narc_files(rom_files["a/1/2/5"], "a/1/2/5")
    if not zone_files:
        return resolved_models, tuple(placed_doors)
    zone_data = zone_files[0]
    location = _matrix_map_locations(matrices, zone_data).get(selected_map)
    if location is None:
        return resolved_models, tuple(placed_doors)
    _matrix_index, zone_index, cell_x, cell_y = location
    event_index = _zone_event_index(zone_data, zone_index)
    if event_index is None or event_index >= len(events):
        return resolved_models, tuple(placed_doors)
    profiles = _learn_door_profiles(str(Path(rom_path).resolve()), rom_files)
    if not profiles:
        return resolved_models, tuple(placed_doors)
    center_x = cell_x * 512 + 256
    center_z = cell_y * 512 + 256
    warps = [
        (
            warp,
            float(warp.x - center_x),
            float(warp.y),
            float(warp.z - center_z),
        )
        for warp in _parse_gen5_warps(events[event_index])
        if warp.width == 1
        and warp.height == 1
        and abs(warp.x - center_x) <= 256
        and abs(warp.z - center_z) <= 256
    ]
    # Remove event entrances already explained by authored AB doors.
    unmatched_warps: list[tuple[_Gen5Warp, float, float, float]] = []
    explicit_world = [
        _placed_door_world_point(
            placement,
            next(
                door.translation
                for door in placed_doors
                if door.placement_index == placement.index
            ),
        )
        for placement in placements
        if placement.index in explicit_placements
    ]
    for warp, world_x, world_y, world_z in warps:
        if any(
            ((world_x - point[0]) ** 2 + (world_z - point[2]) ** 2) ** 0.5 <= 64.0
            for point in explicit_world
        ):
            continue
        unmatched_warps.append((warp, world_x, world_y, world_z))
    candidates = [
        placement
        for placement in placements
        if placement.index not in explicit_placements
        and (model := resolved_models.get(placement.model_index)) is not None
        and _looks_like_entrance_building(model.name)
    ]
    used_placements: set[int] = set()
    for warp, world_x, world_y, world_z in unmatched_warps:
        family = _interior_family_for_zone(
            warp.destination_zone, zone_data, matrices, maps
        )
        available = [
            placement for placement in candidates if placement.index not in used_placements
        ]
        if not available:
            continue
        placement = min(
            available,
            key=lambda item: (
                (world_x - item.x) ** 2 + (world_z - (-item.z)) ** 2,
                item.index,
            ),
        )
        distance = (
            (world_x - placement.x) ** 2 + (world_z - (-placement.z)) ** 2
        ) ** 0.5
        if distance > 80.0:
            continue
        building = resolved_models[placement.model_index]
        profile = profiles.get(family) or profiles.get(
            f"model:{_building_archetype(building.name)}"
        )
        if profile is None:
            continue
        door_model = resolved_models.get(profile.door_model_index)
        if door_model is None or door_model.name != profile.door_model_name:
            door_model = None
            for payload in model_packs:
                try:
                    candidate = parse_ab_building_pack(payload).get(profile.door_model_index)
                except ValueError:
                    continue
                if candidate is not None and candidate.name == profile.door_model_name:
                    door_model = candidate
                    resolved_models[candidate.index] = candidate
                    break
        if door_model is None:
            continue
        local = _placement_local_point(
            placement, world_x, world_y, world_z
        )
        translation = tuple(
            local[index] + profile.correction[index] for index in range(3)
        )
        used_placements.add(placement.index)
        placed_doors.append(
            Gen5PlacedDoor(
                placement_index=placement.index,
                model=door_model,
                translation=translation,
                source="event_warp",
                confidence=profile.confidence,
                destination_zone=warp.destination_zone,
                interior_family=family,
            )
        )
    return resolved_models, tuple(sorted(placed_doors, key=lambda item: item.placement_index))


def resolve_gen5_map_objects(
    rom_path: str | Path,
    virtual_path: str,
    *,
    map_index_override: int | None = None,
    area_index_override: int | None = None,
) -> Gen5MapObjectSet:
    """Resolve a selected ``a/0/0/8`` map to its exact building pack."""
    selected_map = map_index_override
    if selected_map is None:
        selected_map = map_file_index(virtual_path)
    if selected_map is None:
        raise ValueError("This model is not a carved a/0/0/8 Gen 5 map")

    from .rom import NDSRom

    rom = NDSRom.from_path(str(rom_path))
    rom_files = {item.path: item.data for item in rom.iter_files()}
    required = ("a/0/0/8", "a/0/0/9", "a/0/1/2", "a/0/1/3", "a/0/1/4")
    missing = [path for path in required if path not in rom_files]
    if missing:
        raise ValueError(f"ROM does not contain the expected Gen 5 map metadata: {', '.join(missing)}")

    maps = _narc_files(rom_files["a/0/0/8"], "a/0/0/8")
    if selected_map >= len(maps):
        raise ValueError(f"Map file {selected_map} is outside a/0/0/8")
    map_container = maps[selected_map]
    container = parse_gen5_map_container(map_container)
    try:
        placements = parse_map_placements(map_container)
    except ValueError:
        # Some map variants use a different final section. Exact terrain
        # texture/animation resolution is still useful even without objects.
        placements = ()
    terrain_data = bytes(map_container[container.terrain_start : container.terrain_end])

    matrices = _narc_files(rom_files["a/0/0/9"], "a/0/0/9")
    zone_files = _narc_files(rom_files["a/0/1/2"], "a/0/1/2")
    if not zone_files:
        raise ValueError("ZoneData archive a/0/1/2 is empty")
    map_textures = _narc_files(rom_files["a/0/1/4"], "a/0/1/4")
    zone_data = zone_files[0]
    references = _matrix_zone_references(matrices, zone_data)
    reference = references.get(selected_map)
    selected_name = _terrain_model_name(map_container)
    if reference is None:
        canonical, prefix, _season = _variant_name_parts(selected_name)
        names: dict[int, str] = {}
        for index, payload in enumerate(maps):
            try:
                names[index] = _terrain_model_name(payload)
            except ValueError:
                continue
        # Seasonal/snow geometry is intentionally absent from the live matrix.
        # Resolve it through the coordinate-identical spring map first.
        for index, name in names.items():
            candidate_key, candidate_prefix, _candidate_slot = _variant_name_parts(name)
            if candidate_key == canonical and candidate_prefix == "map" and index in references:
                reference = references[index]
                break
        # Some route edge cells are stored in a/0/0/8 but omitted from the
        # shipped matrix.  The nearest coordinate cell carries their AreaData.
        coordinate = re.fullmatch(r"(\d+)_(\d+)", canonical)
        if reference is None and coordinate:
            x, y = (int(value) for value in coordinate.groups())
            selected_materials = _requested_texture_names(terrain_data)
            nearest: list[tuple[int, float, int, tuple[int, int]]] = []
            for index, name in names.items():
                candidate_key, candidate_prefix, _candidate_slot = _variant_name_parts(name)
                candidate_coordinate = re.fullmatch(r"(\d+)_(\d+)", candidate_key)
                if candidate_prefix != "map" or candidate_coordinate is None or index not in references:
                    continue
                candidate_x, candidate_y = (int(value) for value in candidate_coordinate.groups())
                try:
                    candidate_container = parse_gen5_map_container(maps[index])
                    candidate_materials = _requested_texture_names(
                        maps[index][
                            candidate_container.terrain_start : candidate_container.terrain_end
                        ]
                    )
                except ValueError:
                    candidate_materials = set()
                union = selected_materials | candidate_materials
                similarity = len(selected_materials & candidate_materials) / max(1, len(union))
                nearest.append(
                    (
                        abs(candidate_x - x) + abs(candidate_y - y),
                        similarity,
                        index,
                        references[index],
                    )
                )
            if nearest:
                minimum_distance = min(item[0] for item in nearest)
                local = [item for item in nearest if item[0] <= minimum_distance + 2]
                reference = max(local, key=lambda item: (item[1], -item[0], -item[2]))[3]
    area_data = rom_files["a/0/1/3"]
    if reference is not None:
        matrix_index, zone_index = reference
        if zone_index * _ZONE_RECORD_SIZE + _ZONE_RECORD_SIZE > len(zone_data):
            raise ValueError(f"Zone header {zone_index} is outside a/0/1/2")
        zone = zone_data[
            zone_index * _ZONE_RECORD_SIZE : (zone_index + 1) * _ZONE_RECORD_SIZE
        ]
        area = parse_area_data(area_data, struct.unpack_from("<H", zone, 2)[0])
        # A coordinate-neighbour inference is only provisional. If its
        # building pack cannot satisfy this map's placed model IDs, prefer the
        # exact texture/model-score fallback instead.
        if selected_map not in references and placements and area.is_outside:
            outside_model_packs = _gen5_outside_model_packs(rom_files)
            try:
                inferred_models = parse_ab_building_pack(outside_model_packs[area.building_pack])
            except (IndexError, ValueError):
                inferred_models = {}
            inferred_missing = {
                placement.model_index
                for placement in placements
                if placement.model_index not in inferred_models
            }
            if inferred_missing:
                fallback = _fallback_area_for_map(
                    terrain_data=terrain_data,
                    placements=placements,
                    area_data=area_data,
                    map_textures=map_textures,
                    outside_model_packs=outside_model_packs,
                )
                if fallback is not None:
                    try:
                        fallback_models = parse_ab_building_pack(
                            outside_model_packs[fallback.building_pack]
                        )
                    except (IndexError, ValueError):
                        fallback_models = {}
                    fallback_missing = {
                        placement.model_index
                        for placement in placements
                        if placement.model_index not in fallback_models
                    }
                    if len(fallback_missing) < len(inferred_missing):
                        area = fallback
                        matrix_index, zone_index = -1, -1
    else:
        outside_model_packs = _gen5_outside_model_packs(rom_files)
        area = _fallback_area_for_map(
            terrain_data=terrain_data,
            placements=placements,
            area_data=area_data,
            map_textures=map_textures,
            outside_model_packs=outside_model_packs,
        )
        if area is None:
            raise ValueError(
                f"Map file {selected_map} is an unreferenced auxiliary model and its exact texture pack is not present in this ROM"
            )
        matrix_index, zone_index = -1, -1

    seasons = _matching_season_areas(area_data, area)
    if area_index_override is not None:
        area = parse_area_data(area_data, area_index_override)
    else:
        _canonical, selected_prefix, selected_slot = _variant_name_parts(selected_name)
        if selected_slot is not None and len(seasons) == 4:
            area = seasons[selected_slot]

    if area.map_texture >= len(map_textures):
        raise ValueError(f"Map texture {area.map_texture} is outside a/0/1/4")
    material_animation_data = None
    additional_material_animation_data: list[bytes] = []
    if area.translate_animation != 0xFF and "a/0/6/8" in rom_files:
        animations = _narc_files(rom_files["a/0/6/8"], "a/0/6/8")
        if area.translate_animation < len(animations):
            candidate = animations[area.translate_animation]
            if candidate[:4] == b"BTA0":
                material_animation_data = bytes(candidate)
    pattern_animation_data = None
    if area.sequential_animation != 0xFF and "a/0/6/9" in rom_files:
        patterns = _narc_files(rom_files["a/0/6/9"], "a/0/6/9")
        if area.sequential_animation < len(patterns):
            candidate = bytes(patterns[area.sequential_animation])
            # Black/White's sequential archive is not homogeneous. Some
            # entries are ordinary Nitro BTA0 UV animation files (not the
            # custom texture-pattern container used by other AreaData rows).
            # Route by the payload magic so grass/wind motion is not silently
            # handed to the wrong decoder.
            if candidate[:4] == b"BTA0":
                if material_animation_data is None:
                    material_animation_data = candidate
                else:
                    additional_material_animation_data.append(candidate)
            else:
                pattern_animation_data = candidate

    model_archive_path, texture_archive_path, model_packs, texture_packs = (
        _gen5_building_archives(rom_files, outside=area.is_outside)
    )
    placement_ids = {item.model_index for item in placements}
    if placement_ids:
        pack_scores: list[tuple[int, int, int, int]] = []
        for pack_index, payload in enumerate(model_packs):
            try:
                definitions = set(parse_ab_building_pack(payload))
            except ValueError:
                continue
            resolved_count = len(placement_ids & definitions)
            # Prefer complete coverage, then the AreaData index when it is
            # genuinely usable, then the nearest pack for deterministic ties.
            pack_scores.append(
                (
                    resolved_count,
                    int(pack_index == area.building_pack),
                    -abs(pack_index - area.building_pack),
                    -pack_index,
                )
            )
        if pack_scores:
            best_score = max(pack_scores)
            if best_score[0] > 0:
                best_pack = -best_score[3]
                if best_pack != area.building_pack:
                    area = replace(area, building_pack=best_pack)
    if area.building_pack >= len(model_packs) or area.building_pack >= len(texture_packs):
        raise ValueError(f"Building pack {area.building_pack} is outside its model/texture archive")
    try:
        models = parse_ab_building_pack(model_packs[area.building_pack])
        models = _complete_referenced_door_models(models, model_packs)
    except ValueError:
        # Some AreaData records point at an empty/non-AB placeholder building
        # pack. The terrain and its AreaData textures/animations are still
        # exact and exportable. Keep the placement IDs in
        # ``unresolved_model_indices`` so the UI reports the missing objects
        # instead of making the entire map (and its extractable terrain) fail.
        models = {}
    models, placed_doors = _resolve_placed_doors(
        rom_path=rom_path,
        rom_files=rom_files,
        selected_map=selected_map,
        placements=placements,
        models=models,
        model_packs=model_packs,
    )
    missing_models = sorted({item.model_index for item in placements if item.model_index not in models})
    variants = _map_variants(maps, selected_map, area_data, area)
    variant_key = f"{selected_map}:{area.index}"
    variant_label = next(
        (variant.label for variant in variants if variant.key == variant_key),
        _SPECIAL_VARIANT_LABELS.get(
            _variant_name_parts(selected_name)[1],
            _SEASON_PREFIXES.get(_variant_name_parts(selected_name)[1], (None, "Default"))[1],
        ),
    )
    return Gen5MapObjectSet(
        map_file_index=selected_map,
        matrix_index=matrix_index,
        zone_index=zone_index,
        area=area,
        placements=placements,
        models=models,
        doors=placed_doors,
        unresolved_model_indices=tuple(missing_models),
        terrain_data=terrain_data,
        map_texture_data=bytes(map_textures[area.map_texture]),
        material_animation_data=material_animation_data,
        additional_material_animation_data=tuple(additional_material_animation_data),
        pattern_animation_data=pattern_animation_data,
        texture_data=bytes(texture_packs[area.building_pack]),
        model_archive_path=model_archive_path,
        texture_archive_path=texture_archive_path,
        variant_key=variant_key,
        variant_label=variant_label,
        variants=variants,
    )


def build_gen5_map_composition(
    rom_path: str | Path,
    virtual_path: str,
    terrain_glb: Path,
    out_dir: Path,
    *,
    progress: Progress | None = None,
    map_index_override: int | None = None,
    area_index_override: int | None = None,
) -> Gen5MapComposition:
    """Convert placed buildings and combine them with the current terrain GLB."""
    from .exporter import convert_with_apicula
    from .gltf.compose import GlbScenePart, compose_glb_scenes
    from .gltf.embed_textures import embed_glb_external_images
    from .gltf.glb_io import read_glb
    from .gltf.merge_animations import (
        merge_glb_skeletal_animations,
        retain_default_skeletal_animation,
        scale_glb_skeletal_animation_durations,
        strip_all_animations,
    )

    if not terrain_glb.is_file():
        raise ValueError("Preview the terrain model before loading its placed objects")
    if progress:
        progress("Resolving Gen 5 area, zone, and building pack…")
    objects = resolve_gen5_map_objects(
        rom_path,
        virtual_path,
        map_index_override=map_index_override,
        area_index_override=area_index_override,
    )
    if out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        area_bta_count = int(objects.material_animation_data is not None) + len(
            objects.additional_material_animation_data
        )
        progress(
            f"Converting terrain with exact AreaData texture {objects.area.map_texture}"
            + (f", {area_bta_count} BTA0 animation resource(s)" if area_bta_count else "")
            + (f", and pattern animation {objects.area.sequential_animation}…" if objects.pattern_animation_data else "…")
        )
    terrain_asset = Asset(
        asset_id=f"gen5-map-{objects.map_file_index}-terrain",
        virtual_path=f"a/0/0/8/file_{objects.map_file_index:04d}.bin#terrain.nsbmd",
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=objects.terrain_data,
        original_data=objects.terrain_data,
        carved=True,
    )
    map_texture_asset = Asset(
        asset_id=f"gen5-map-texture-{objects.area.map_texture}",
        virtual_path=f"a/0/1/4/file_{objects.area.map_texture:04d}.bin.nsbtx",
        kind="Texture",
        magic="BTX0",
        extension=".nsbtx",
        data=objects.map_texture_data,
        original_data=objects.map_texture_data,
    )
    terrain_siblings = [map_texture_asset]
    area_bta_files = [
        *([objects.material_animation_data] if objects.material_animation_data else []),
        *objects.additional_material_animation_data,
    ]
    material_animation_assets = [
        Asset(
            asset_id=f"gen5-map-animation-{objects.map_file_index}-{index}",
            virtual_path=f"area_bta_{index:02d}.nsbta",
            kind="Texture SRT animation",
            magic="BTA0",
            extension=".nsbta",
            data=payload,
            original_data=payload,
        )
        for index, payload in enumerate(area_bta_files)
    ]
    terrain_siblings.extend(material_animation_assets)
    if objects.pattern_animation_data:
        terrain_siblings.append(
            Asset(
                asset_id=f"gen5-map-pattern-{objects.area.sequential_animation}",
                virtual_path=f"a/0/6/9/file_{objects.area.sequential_animation:04d}.bin.pattern",
                kind="Texture pattern animation",
                magic="PATTERN",
                extension=".pattern",
                data=objects.pattern_animation_data,
                original_data=objects.pattern_animation_data,
            )
        )
    terrain_dir = out_dir / "terrain"
    terrain_result = convert_with_apicula(
        terrain_asset,
        terrain_dir,
        sibling_assets=terrain_siblings,
        output_format="glb",
        more_textures=False,
    )
    exact_terrain = next(
        (path for path in terrain_result.output_files if path.suffix.casefold() == ".glb" and path.is_file()),
        None,
    )
    if terrain_result.ok and exact_terrain is not None:
        embedded_terrain = terrain_dir / "terrain_exact_embedded.glb"
        embed_glb_external_images(
            read_glb(exact_terrain),
            base_dir=exact_terrain.parent,
            search_paths=[path for path in terrain_dir.iterdir() if path.is_file()],
            require_all=True,
        ).write(embedded_terrain)
        from .material_animation import attach_gen5_pattern_motion

        if objects.pattern_animation_data:
            attach_gen5_pattern_motion(embedded_terrain, objects.pattern_animation_data)
        terrain_glb = embedded_terrain
    elif progress:
        progress("Exact terrain conversion failed; retaining the existing terrain preview as a fallback.")
    texture_asset = Asset(
        asset_id=f"gen5-building-textures-{objects.area.building_pack}",
        virtual_path=f"{objects.texture_archive_path}/file_{objects.area.building_pack:04d}.bin.nsbtx",
        kind="Texture",
        magic="BTX0",
        extension=".nsbtx",
        data=objects.texture_data,
        original_data=objects.texture_data,
    )

    converted: dict[int, Path] = {}
    composed_sources: dict[int, Path] = {}
    model_bta_files: dict[int, list[bytes]] = {}
    model_btp_files: dict[int, list[bytes]] = {}
    model_pattern_images: dict[int, list[Path]] = {}
    placed_model_indices = {
        item.model_index for item in objects.placements if item.model_index in objects.models
    }
    placement_by_index = {placement.index: placement for placement in objects.placements}
    door_by_placement = {door.placement_index: door for door in objects.doors}
    conversion_indices = placed_model_indices | {
        door.model.index for door in door_by_placement.values()
    }
    door_model_indices = {door.model.index for door in door_by_placement.values()}
    for model_index in sorted(conversion_indices):
        model = objects.models[model_index]
        if progress:
            progress(f"Converting placed model {model_index}: {model.name}…")
        model_asset = Asset(
            asset_id=f"gen5-map-{objects.map_file_index}-building-{model_index}",
            virtual_path=(
                f"{objects.model_archive_path}/file_{objects.area.building_pack:04d}.bin"
                f"#building_{model_index:02d}_{model.name}.nsbmd"
            ),
            kind="Model",
            magic="BMD0",
            extension=".nsbmd",
            data=model.data,
            original_data=model.data,
        )
        model_animation_assets: list[Asset] = []
        for resource_index, (magic, animation_data) in enumerate(
            embedded_model_animation_resources(model.metadata)
        ):
            kind, extension = _MODEL_ANIMATION_INFO[magic.encode("ascii")]
            model_animation_assets.append(
                Asset(
                    asset_id=(
                        f"gen5-map-{objects.map_file_index}-building-{model_index}-"
                        f"animation-{resource_index}"
                    ),
                    virtual_path=(
                        f"{objects.model_archive_path}/file_{objects.area.building_pack:04d}.bin"
                        f"#building_{model_index:02d}_{model.name}_animation_{resource_index}{extension}"
                    ),
                    kind=kind,
                    magic=magic,
                    extension=extension,
                    data=animation_data,
                    original_data=animation_data,
                )
            )
        model_bta_files[model_index] = [
            asset.data for asset in model_animation_assets if asset.magic == "BTA0"
        ]
        model_btp_files[model_index] = [
            asset.data for asset in model_animation_assets if asset.magic == "BTP0"
        ]
        model_dir = out_dir / f"model_{model_index:02d}"
        result = convert_with_apicula(
            model_asset,
            model_dir,
            # Area BTA tracks can target placed-object materials (the WBT
            # fountain is the canonical example), not only terrain materials.
            sibling_assets=[
                texture_asset,
                *material_animation_assets,
                *model_animation_assets,
            ],
            output_format="glb",
            more_textures=False,
            all_animations=True,
        )
        glb = next((path for path in result.output_files if path.suffix.casefold() == ".glb" and path.is_file()), None)
        if not result.ok or glb is None:
            raise RuntimeError(result.message or f"Could not convert building model {model_index}")
        embedded_glb = model_dir / f"{model.name}_embedded.glb"
        embed_glb_external_images(
            read_glb(glb),
            base_dir=glb.parent,
            search_paths=[path for path in model_dir.iterdir() if path.is_file()],
            require_all=True,
        ).write(embedded_glb)
        # Preserve alternate states (day/evening/night, door open/close, etc.)
        # for standalone preview/export. Merging them made incompatible states
        # run simultaneously and could hide an otherwise static backdrop.
        scale_glb_skeletal_animation_durations(
            read_glb(embedded_glb),
            duration_scale=2.0,
        ).write(embedded_glb)
        pattern_images = [path for path in model_dir.iterdir() if path.suffix.casefold() == ".png"]
        model_pattern_images[model_index] = pattern_images
        if model_btp_files[model_index]:
            from .material_animation import attach_btp0_pattern_motion

            attach_btp0_pattern_motion(
                embedded_glb,
                model_btp_files[model_index],
                pattern_images,
            )
        default_glb = model_dir / f"{model.name}_default_state.glb"
        retain_default_skeletal_animation(read_glb(embedded_glb)).write(default_glb)
        converted[model_index] = embedded_glb
        composed_sources[model_index] = default_glb

    # A Gen 5 door is an independent animated model referenced by the
    # building's AB definition. Assemble it for standalone preview/export, and
    # assemble its closed/default state for the exact map. This keeps door
    # open/close clips available on a building GLB without forcing every state
    # to play simultaneously in the full-map ambient animation.
    preview_sources_by_placement: dict[int, Path] = {}
    composed_sources_by_placement: dict[int, Path] = {}
    for placement_index, door in door_by_placement.items():
        placement = placement_by_index.get(placement_index)
        if placement is None or placement.model_index not in converted:
            continue
        building_index = placement.model_index
        building = objects.models[building_index]
        assembly_dir = out_dir / f"placement_{placement_index:02d}_assembly"
        assembly_dir.mkdir(parents=True, exist_ok=True)
        standalone = assembly_dir / f"{building.name}_with_{door.model.name}.glb"
        compose_glb_scenes(
            [
                GlbScenePart(converted[building_index], building.name),
                GlbScenePart(
                    converted[door.model.index],
                    f"door_{door.model.name}",
                    translation=door.translation,
                ),
            ],
            standalone,
        )
        closed_door = assembly_dir / f"{door.model.name}_closed.glb"
        strip_all_animations(read_glb(converted[door.model.index])).write(closed_door)
        default = assembly_dir / f"{building.name}_with_door_default.glb"
        compose_glb_scenes(
            [
                GlbScenePart(composed_sources[building_index], building.name),
                GlbScenePart(
                    closed_door,
                    f"door_{door.model.name}",
                    translation=door.translation,
                ),
            ],
            default,
        )
        preview_sources_by_placement[placement_index] = standalone
        composed_sources_by_placement[placement_index] = default

    if progress:
        progress(f"Composing {len(objects.placements)} placed object(s) with the terrain…")
    scene_parts = [GlbScenePart(terrain_glb, "terrain")]
    previews: list[Gen5ObjectPreview] = []
    for placement in objects.placements:
        if placement.model_index not in converted:
            continue
        model = objects.models[placement.model_index]
        glb = preview_sources_by_placement.get(
            placement.index, converted[placement.model_index]
        )
        scene_parts.append(
            GlbScenePart(
                composed_sources_by_placement.get(
                    placement.index, composed_sources[placement.model_index]
                ),
                f"object_{placement.index:02d}_{model.name}",
                translation=(placement.x, placement.y, -placement.z),
                rotation_degrees=placement.rotation_degrees,
            )
        )
        previews.append(
            Gen5ObjectPreview(
                placement=placement,
                model=model,
                glb_path=glb,
                door=door_by_placement.get(placement.index),
            )
        )

    composed_glb = out_dir / f"map_{objects.map_file_index:04d}_with_objects.glb"
    compose_glb_scenes(scene_parts, composed_glb)
    # Placed props animate concurrently in-game.  Combine their channels into
    # one action the viewer can play. Standalone inputs were already slowed.
    merge_glb_skeletal_animations(
        read_glb(composed_glb),
        duration_scale=1.0,
    ).write(composed_glb)
    from .material_animation import (
        attach_bta0_material_motion,
        attach_btp0_pattern_motion,
        attach_gen5_pattern_motion,
    )

    # Resolve against the final material table so terrain and separately placed
    # object effects participate in the same exact AreaData clip.
    bta_files = [
        *area_bta_files,
        *(
            animation
            for model_index in converted
            if model_index not in door_model_indices
            for animation in model_bta_files.get(model_index, [])
        ),
    ]
    if bta_files:
        attach_bta0_material_motion(composed_glb, bta_files)
    if objects.pattern_animation_data:
        attach_gen5_pattern_motion(composed_glb, objects.pattern_animation_data)
    btp_files = [
        animation
        for model_index in converted
        for animation in model_btp_files.get(model_index, [])
    ]
    if btp_files:
        attach_btp0_pattern_motion(
            composed_glb,
            btp_files,
            [
                path
                for model_index in converted
                for path in model_pattern_images.get(model_index, [])
            ],
        )
    if progress:
        progress(f"Loaded {len(previews)} placed object(s) from building pack {objects.area.building_pack}.")
    return Gen5MapComposition(
        objects=objects,
        terrain_glb=terrain_glb,
        composed_glb=composed_glb,
        previews=tuple(previews),
    )
