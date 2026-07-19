"""Pokémon Gen 5 map-object discovery and preview composition.

This is intentionally an NDS platform island.  It reads the map/zone/building
archives from a Gen 5 ROM, but does not participate in the normal model renderer.
"""
from __future__ import annotations

import re
import shutil
import struct
from dataclasses import dataclass
from pathlib import Path
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
    unresolved_model_indices: tuple[int, ...]
    terrain_data: bytes
    map_texture_data: bytes
    material_animation_data: bytes | None
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
            outside_model_packs = _narc_files(rom_files["a/2/2/5"], "a/2/2/5")
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
        outside_model_packs = (
            _narc_files(rom_files["a/2/2/5"], "a/2/2/5")
            if "a/2/2/5" in rom_files
            else []
        )
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
            pattern_animation_data = bytes(patterns[area.sequential_animation])

    model_archive_path = "a/2/2/5" if area.is_outside else "a/2/2/6"
    texture_archive_path = "a/1/7/4" if area.is_outside else "a/1/7/5"
    if model_archive_path not in rom_files or texture_archive_path not in rom_files:
        raise ValueError("ROM is missing the building model or texture archive selected by AreaData")
    model_packs = _narc_files(rom_files[model_archive_path], model_archive_path)
    texture_packs = _narc_files(rom_files[texture_archive_path], texture_archive_path)
    if area.building_pack >= len(model_packs) or area.building_pack >= len(texture_packs):
        raise ValueError(f"Building pack {area.building_pack} is outside its model/texture archive")
    models = parse_ab_building_pack(model_packs[area.building_pack])
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
        unresolved_model_indices=tuple(missing_models),
        terrain_data=terrain_data,
        map_texture_data=bytes(map_textures[area.map_texture]),
        material_animation_data=material_animation_data,
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
        progress(
            f"Converting terrain with exact AreaData texture {objects.area.map_texture}"
            + (f", BTA0 animation {objects.area.translate_animation}" if objects.material_animation_data else "")
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
    material_animation_asset = None
    if objects.material_animation_data:
        material_animation_asset = Asset(
            asset_id=f"gen5-map-animation-{objects.area.translate_animation}",
            virtual_path=f"a/0/6/8/file_{objects.area.translate_animation:04d}.bin.nsbta",
            kind="Texture SRT animation",
            magic="BTA0",
            extension=".nsbta",
            data=objects.material_animation_data,
            original_data=objects.material_animation_data,
        )
        terrain_siblings.append(material_animation_asset)
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
    for model_index in sorted({item.model_index for item in objects.placements if item.model_index in objects.models}):
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
                *([material_animation_asset] if material_animation_asset else []),
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

    if progress:
        progress(f"Composing {len(objects.placements)} placed object(s) with the terrain…")
    scene_parts = [GlbScenePart(terrain_glb, "terrain")]
    previews: list[Gen5ObjectPreview] = []
    for placement in objects.placements:
        if placement.model_index not in converted:
            continue
        model = objects.models[placement.model_index]
        glb = converted[placement.model_index]
        scene_parts.append(
            GlbScenePart(
                composed_sources[placement.model_index],
                f"object_{placement.index:02d}_{model.name}",
                translation=(placement.x, placement.y, -placement.z),
                rotation_degrees=placement.rotation_degrees,
            )
        )
        previews.append(Gen5ObjectPreview(placement=placement, model=model, glb_path=glb))

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
        *([objects.material_animation_data] if objects.material_animation_data else []),
        *(
            animation
            for model_index in converted
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
