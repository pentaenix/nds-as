"""Logical Pokémon Generation V map catalog for Models Resource exports."""
from __future__ import annotations

import csv
import json
import re
import struct
from collections import defaultdict, deque
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ..map_objects import (
    _matrix_all_map_locations,
    _narc_files,
    _terrain_model_name,
    _zone_event_index,
    parse_area_data,
)
from ..rom import NDSRom
from .models_resource_interior_names import (
    COMPOUND_LOCATIONS,
    COMPOUND_TITLES,
    compound_scene_label,
    is_white2_variant,
    suggest_interior_name,
)

_ZONE_SIZE = 48
_LOCATION_BANK = 109


@dataclass(frozen=True)
class MapCell:
    map_id: int
    matrix: int
    zone: int
    x: int
    y: int
    area: int
    offset_x: float | None = None
    offset_z: float | None = None
    include_objects: bool = True
    additional_map_textures: tuple[int, ...] = ()
    max_component_center_x: float | None = None
    remove_black_vertex_colors: bool = False


@dataclass(frozen=True)
class MapScene:
    name: str
    cells: tuple[MapCell, ...]


@dataclass(frozen=True)
class MapSubmission:
    key: str
    title: str
    section: str
    location_name: str
    confidence: str
    notes: str
    scenes: tuple[MapScene, ...]
    combine_scenes_in_dae: bool = False
    preview_yaw: float = 0.0


def _rotate_left_16(value: int, count: int) -> int:
    return ((value << count) | (value >> (16 - count))) & 0xFFFF


def decode_gen5_text_bank(data: bytes) -> list[str]:
    """Decode a Generation V encrypted UTF-16 message bank."""
    if len(data) < 20:
        raise ValueError("Generation V text bank is truncated")
    section_count, entry_count = struct.unpack_from("<HH", data, 0)
    if section_count < 1:
        return []
    section_offset = struct.unpack_from("<I", data, 12)[0]
    table = section_offset + 4
    if table + entry_count * 8 > len(data):
        raise ValueError("Generation V text entry table is truncated")
    result: list[str] = []
    for entry_index in range(entry_count):
        relative, word_count, _flags = struct.unpack_from("<IHH", data, table + entry_index * 8)
        start = section_offset + relative
        if start + word_count * 2 > len(data):
            raise ValueError("Generation V text entry is truncated")
        key = (0x2983 * (entry_index + 3)) & 0xFFFF
        words: list[int] = []
        for position in range(word_count):
            encrypted = struct.unpack_from("<H", data, start + position * 2)[0]
            words.append(encrypted ^ key)
            key = _rotate_left_16(key, 3)
        result.append(_decode_message_words(words))
    return result


def _decode_message_words(words: list[int]) -> str:
    values: list[int] = []
    if words and words[0] == 0xF100:
        accumulator = 0
        bits = 0
        for word in words[1:]:
            accumulator |= word << bits
            bits += 16
            while bits >= 9:
                value = accumulator & 0x1FF
                accumulator >>= 9
                bits -= 9
                if value == 0x1FF:
                    break
                values.append(value)
    else:
        values = words
    characters: list[str] = []
    for value in values:
        if value == 0xFFFF:
            break
        if value == 0xFFFE:
            characters.append("\n")
        elif value < 0xD800 or 0xE000 <= value <= 0xFFFD:
            characters.append(chr(value))
    return "".join(characters).strip()


def _components(cells: list[MapCell]) -> list[tuple[MapCell, ...]]:
    remaining = {(cell.x, cell.y): cell for cell in cells}
    found: list[tuple[MapCell, ...]] = []
    while remaining:
        start = next(iter(remaining))
        queue = deque([start])
        component: list[MapCell] = []
        while queue:
            coordinate = queue.popleft()
            cell = remaining.pop(coordinate, None)
            if cell is None:
                continue
            component.append(cell)
            x, y = coordinate
            queue.extend(((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)))
        found.append(tuple(sorted(component, key=lambda item: (item.y, item.x, item.map_id))))
    return sorted(found, key=lambda item: (-len(item), item[0].y, item[0].x))


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "unnamed"


_GENERIC_INTERIOR_LABELS = {
    "battle room",
    "classroom",
    "dining room",
    "gatehouse",
    "interior",
    "laboratory",
    "main hall",
    "main office",
    "main room",
    "office",
    "terminal",
}


def _standalone_interior_title(package: str, scene: str) -> str:
    """Name one loading-zone-separated interior as its own submission."""
    label = scene.strip()
    if not label or label.casefold() in _GENERIC_INTERIOR_LABELS:
        return package
    if label.casefold() == "entrance":
        return package
    if label.casefold() in package.casefold():
        return package
    return f"{package} {label}"


def _unique_title(title: str, seen: dict[str, int]) -> str:
    seen[title] = seen.get(title, 0) + 1
    return title if seen[title] == 1 else f"{title} Area {seen[title]}"


_STITCHABLE_CAVE_LOCATIONS = {
    "Chargestone Cave",
    "Clay Tunnel",
    "Giant Chasm",
    "Mistralton Cave",
    "Relic Castle",
    "Relic Passage",
    "Reversal Mountain",
    "Seaside Cave",
    "Twist Mountain",
    "Victory Road",
    "Wellspring Cave",
}
_HORIZONTAL_CAVE_TRANSITIONS = {1025, 1026}
_IndependentInterior = tuple[int, MapScene, str, str, str, str]


def _event_warps(event: bytes) -> tuple[tuple[int, int, int, int, int, int], ...]:
    """Return destination, destination-warp, transition, and XYZ for one zone."""
    if len(event) < 8:
        return ()
    furniture_count, actor_count, warp_count = event[4:7]
    start = 8 + furniture_count * 20 + actor_count * 36
    if start + warp_count * 20 > len(event):
        return ()
    return tuple(
        struct.unpack_from("<HHH2xhhh", event, start + index * 20)
        for index in range(warp_count)
    )


def _combined_area_label(labels: list[str]) -> str:
    numbers = [int(match.group(1)) for label in labels if (match := re.fullmatch(r"Area (\d+)", label))]
    if len(numbers) == len(labels):
        numbers.sort()
        return f"Areas {numbers[0]}-{numbers[-1]}"
    return "Combined Area"


def _stitch_same_floor_cave_rows(
    rows: list[_IndependentInterior],
    *,
    name_by_index: dict[int, str],
    zone_data: bytes,
    rom_files: dict[str, bytes],
) -> list[_IndependentInterior]:
    """Merge cave matrices connected by reciprocal horizontal portals.

    Stair/lift transition IDs are deliberately excluded. Multiple portals
    between the same pair must agree on one translation; nonlinear/reused room
    layouts therefore remain separate instead of being forced to overlap.
    """
    event_path = next(
        (path for path in ("a/1/2/6", "a/1/2/5") if path in rom_files),
        None,
    )
    if event_path is None:
        return rows
    events = _narc_files(rom_files[event_path], event_path)
    zone_to_row: dict[tuple[str, int], int] = {}
    for index, row in enumerate(rows):
        location = name_by_index[row[0]]
        zones = {cell.zone for cell in row[1].cells}
        if location in _STITCHABLE_CAVE_LOCATIONS and len(zones) == 1:
            zone_to_row[(location, next(iter(zones)))] = index

    warp_cache: dict[int, tuple[tuple[int, int, int, int, int, int], ...]] = {}

    def warps(zone: int) -> tuple[tuple[int, int, int, int, int, int], ...]:
        if zone in warp_cache:
            return warp_cache[zone]
        event_index = _zone_event_index(zone_data, zone)
        warp_cache[zone] = (
            _event_warps(events[event_index])
            if event_index is not None and 0 <= event_index < len(events)
            else ()
        )
        return warp_cache[zone]

    offsets_by_pair: dict[tuple[int, int], list[tuple[float, float]]] = defaultdict(list)
    for (location, zone), source_index in zone_to_row.items():
        for destination_zone, destination_warp, transition, x, _y, z in warps(zone):
            destination_index = zone_to_row.get((location, destination_zone))
            destination_warps = warps(destination_zone)
            if (
                destination_index is None
                or destination_index == source_index
                or transition not in _HORIZONTAL_CAVE_TRANSITIONS
                or not 0 <= destination_warp < len(destination_warps)
            ):
                continue
            back = destination_warps[destination_warp]
            if back[0] != zone:
                continue
            delta = (float(x - back[3]), float(z - back[5]))
            pair = (min(source_index, destination_index), max(source_index, destination_index))
            offsets_by_pair[pair].append(
                delta if source_index < destination_index else (-delta[0], -delta[1])
            )

    adjacency: dict[int, list[tuple[int, tuple[float, float]]]] = defaultdict(list)
    for (left, right), offsets in offsets_by_pair.items():
        if max(value[0] for value in offsets) - min(value[0] for value in offsets) > 8.0:
            continue
        if max(value[1] for value in offsets) - min(value[1] for value in offsets) > 8.0:
            continue
        delta = (
            sum(value[0] for value in offsets) / len(offsets),
            sum(value[1] for value in offsets) / len(offsets),
        )
        adjacency[left].append((right, delta))
        adjacency[right].append((left, (-delta[0], -delta[1])))

    consumed: set[int] = set()
    merged: list[_IndependentInterior] = []
    for start in sorted(adjacency):
        if start in consumed:
            continue
        origins = {start: (0.0, 0.0)}
        queue = deque([start])
        while queue:
            current = queue.popleft()
            for neighbor, delta in adjacency[current]:
                candidate = (origins[current][0] + delta[0], origins[current][1] + delta[1])
                if neighbor not in origins:
                    origins[neighbor] = candidate
                    queue.append(neighbor)
        if len(origins) < 2:
            continue
        footprints: list[tuple[float, float, float, float]] = []
        overlaps_existing_floor = False
        for index, (origin_x, origin_z) in origins.items():
            cells = rows[index][1].cells
            footprint = (
                origin_x + min(cell.x for cell in cells) * 512.0,
                origin_x + (max(cell.x for cell in cells) + 1) * 512.0,
                origin_z + min(cell.y for cell in cells) * 512.0,
                origin_z + (max(cell.y for cell in cells) + 1) * 512.0,
            )
            for other in footprints:
                overlap_x = min(footprint[1], other[1]) - max(footprint[0], other[0])
                overlap_z = min(footprint[3], other[3]) - max(footprint[2], other[2])
                if overlap_x > 256.0 and overlap_z > 256.0:
                    overlaps_existing_floor = True
                    break
            footprints.append(footprint)
            if overlaps_existing_floor:
                break
        if overlaps_existing_floor:
            # Some caves deliberately reuse/nonlinearly connect room space.
            # A flat combined model would place those authored rooms on top of
            # each other, so retain their independent submissions.
            continue
        consumed.update(origins)
        members = sorted(origins)
        labels = [rows[index][3] for index in members]
        cells: list[MapCell] = []
        for index in members:
            origin_x, origin_z = origins[index]
            for cell in rows[index][1].cells:
                cells.append(replace(
                    cell,
                    offset_x=origin_x + cell.x * 512.0,
                    offset_z=origin_z + cell.y * 512.0,
                ))
        first = rows[members[0]]
        label = _combined_area_label(labels)
        cells.sort(key=lambda cell: (cell.offset_z or 0.0, cell.offset_x or 0.0, cell.map_id))
        merged.append((
            first[0],
            MapScene(f"stitched_{first[1].name}", tuple(cells)),
            first[2],
            label,
            first[4],
            "Same-floor cave matrices aligned through reciprocal horizontal warp coordinates.",
        ))

    merged.extend(row for index, row in enumerate(rows) if index not in consumed)
    return merged


def _zone_fields(zone_data: bytes, zone: int) -> tuple[int, int, tuple[int, int] | None]:
    offset = zone * _ZONE_SIZE
    area = struct.unpack_from("<H", zone_data, offset + 2)[0]
    name_index = struct.unpack_from("<H", zone_data, offset + 26)[0] & 0x03FF
    fly_x = struct.unpack_from("<H", zone_data, offset + 36)[0]
    fly_y = struct.unpack_from("<H", zone_data, offset + 44)[0]
    seed = None if fly_x == 0xFFFF or fly_y == 0xFFFF else (fly_x // 32, fly_y // 32)
    return area, name_index, seed


def build_black2_map_catalog(rom_path: Path) -> tuple[list[MapSubmission], dict[str, bytes]]:
    rom_files = {item.path: item.data for item in NDSRom.from_path(str(rom_path)).iter_files()}
    matrices = _narc_files(rom_files["a/0/0/9"], "a/0/0/9")
    map_payloads = _narc_files(rom_files["a/0/0/8"], "a/0/0/8")
    zones = _narc_files(rom_files["a/0/1/2"], "a/0/1/2")[0]
    text_banks = _narc_files(rom_files["a/0/0/2"], "a/0/0/2")
    names = decode_gen5_text_bank(text_banks[_LOCATION_BANK])
    area_data = rom_files["a/0/1/3"]

    outdoor: dict[tuple[int, int], list[MapCell]] = defaultdict(list)
    indoor: dict[tuple[int, int, int], list[MapCell]] = defaultdict(list)
    seeds: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    name_by_index: dict[int, str] = {}
    for map_id, (matrix, zone, x, y) in _matrix_all_map_locations(matrices, zones):
        area, name_index, seed = _zone_fields(zones, zone)
        if not 0 <= name_index < len(names) or not names[name_index]:
            continue
        name_by_index[name_index] = names[name_index]
        cell = MapCell(map_id, matrix, zone, x, y, area)
        if parse_area_data(area_data, area).is_outside:
            outdoor[(name_index, matrix)].append(cell)
            if seed is not None:
                seeds[(name_index, matrix)].add(seed)
        else:
            indoor[(name_index, matrix, zone)].append(cell)

    submissions: list[MapSubmission] = []
    by_location: dict[int, list[MapScene]] = defaultdict(list)
    for (name_index, matrix), cells in outdoor.items():
        components = _components(cells)
        seeded = [
            component for component in components
            if any((cell.x, cell.y) in seeds[(name_index, matrix)] for cell in component)
        ]
        chosen = seeded or components[:1]
        for number, component in enumerate(chosen, 1):
            by_location[name_index].append(MapScene(f"matrix_{matrix:03d}_{number:02d}", component))
    outdoor_by_title: dict[str, list[tuple[int, MapScene]]] = defaultdict(list)
    for name_index, scenes in by_location.items():
        outdoor_by_title[name_by_index[name_index]].extend((name_index, scene) for scene in scenes)
    outdoor_titles: dict[str, int] = {}
    for title, indexed_scenes in outdoor_by_title.items():
        for name_index, scene in sorted(indexed_scenes, key=lambda row: row[1].name):
            standalone_title = _unique_title(title, outdoor_titles)
            submissions.append(MapSubmission(
                key=f"maps:{name_index:03d}:{scene.name}",
                title=standalone_title,
                section="Maps",
                location_name=title,
                confidence="official",
                notes="Official in-ROM location name; one continuous matrix component per submission.",
                scenes=(MapScene("Map", scene.cells),),
            ))

    interior_rows: list[tuple[int, MapScene]] = []
    for (name_index, matrix, zone), cells in indoor.items():
        for number, component in enumerate(_components(cells), 1):
            interior_rows.append((name_index, MapScene(f"matrix_{matrix:03d}_zone_{zone:03d}_{number:02d}", component)))
    def terrain_names(scene: MapScene) -> tuple[str, ...]:
        result: list[str] = []
        for cell in scene.cells:
            try:
                name = _terrain_model_name(map_payloads[cell.map_id])
            except (IndexError, ValueError):
                continue
            if name not in result:
                result.append(name)
        return tuple(result)

    detailed = [
        (name_index, scene, terrain_names(scene))
        for name_index, scene in interior_rows
    ]
    detailed = [row for row in detailed if not is_white2_variant(row[2])]

    compound_by_location: dict[str, list[tuple[int, MapScene, tuple[str, ...]]]] = defaultdict(list)
    remaining: list[tuple[int, MapScene, tuple[str, ...]]] = []
    for row in detailed:
        base = name_by_index[row[0]]
        if base in COMPOUND_LOCATIONS:
            compound_by_location[base].append(row)
        else:
            remaining.append(row)
    independent: list[tuple[int, MapScene, str, str, str, str]] = []
    for base, rows in sorted(compound_by_location.items()):
        rows.sort(key=lambda row: (row[2][0].casefold() if row[2] else "", row[1].name))
        package = COMPOUND_TITLES.get(base, base)
        for index, (name_index, scene, terrain) in enumerate(rows, 1):
            independent.append((
                name_index,
                scene,
                package,
                compound_scene_label(base, terrain, index),
                "high",
                "One loading-zone-separated matrix component; White 2-only variants excluded.",
            ))

    counters: dict[int, int] = defaultdict(int)
    for name_index, scene, terrain in sorted(remaining, key=lambda row: (name_by_index[row[0]], row[1].name)):
        counters[name_index] += 1
        suggestion = suggest_interior_name(name_by_index[name_index], terrain, counters[name_index])
        independent.append((
            name_index,
            scene,
            suggestion.package,
            suggestion.scene,
            suggestion.confidence,
            suggestion.note,
        ))

    independent = _stitch_same_floor_cave_rows(
        independent,
        name_by_index=name_by_index,
        zone_data=zones,
        rom_files=rom_files,
    )
    interior_titles: dict[str, int] = {}
    for name_index, scene, package, label, confidence, note in sorted(
        independent,
        key=lambda row: (row[2].casefold(), row[3].casefold(), row[1].name),
    ):
        title = _unique_title(_standalone_interior_title(package, label), interior_titles)
        submissions.append(MapSubmission(
            key=f"interiors:{name_index:03d}:{_slug(package)}:{scene.name}",
            title=title,
            section="Interior Maps",
            location_name=name_by_index[name_index],
            confidence=confidence,
            notes=f"{note} Exported independently because a loading transition separates it.",
            scenes=(MapScene(label, scene.cells),),
        ))
    from .models_resource_map_corrections import apply_black2_catalog_corrections

    submissions = apply_black2_catalog_corrections(submissions)
    submissions.sort(key=lambda item: (item.section, item.title.casefold(), item.key))
    return submissions, rom_files


def write_review_files(submissions: list[MapSubmission], output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = output_dir / "map_catalog.json"
    catalog_path.write_text(json.dumps([asdict(item) for item in submissions], indent=2) + "\n", encoding="utf-8")
    review_path = output_dir / "map_review.csv"
    previous: dict[str, dict[str, str]] = {}
    if review_path.exists():
        with review_path.open(encoding="utf-8", newline="") as handle:
            previous = {row.get("key", ""): row for row in csv.DictReader(handle)}
    with review_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("include", "key", "section", "title", "confidence", "notes"))
        writer.writeheader()
        for item in submissions:
            old = previous.get(item.key, {})
            writer.writerow({
                "include": old.get("include", "yes"), "key": item.key,
                "section": old.get("section", item.section), "title": old.get("title", item.title),
                "confidence": item.confidence, "notes": old.get("notes", item.notes),
            })
    return catalog_path, review_path


def apply_review(submissions: list[MapSubmission], review_path: Path | None) -> list[MapSubmission]:
    if review_path is None or not review_path.is_file():
        return submissions
    by_key = {item.key: item for item in submissions}
    selected: list[MapSubmission] = []
    with review_path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            item = by_key.get(row.get("key", ""))
            if item is None or row.get("include", "yes").strip().casefold() in {"no", "false", "0", "skip"}:
                continue
            selected.append(MapSubmission(
                key=item.key, title=row.get("title", "").strip() or item.title,
                section=row.get("section", "").strip() or item.section,
                location_name=item.location_name, confidence=item.confidence,
                notes=row.get("notes", "").strip() or item.notes, scenes=item.scenes,
                combine_scenes_in_dae=item.combine_scenes_in_dae,
                preview_yaw=item.preview_yaw,
            ))
    return selected
