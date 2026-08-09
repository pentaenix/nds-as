"""Extract selected material primitives from an NDS-owned GLB."""
from __future__ import annotations

import copy
import math
import re
import struct
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from .glb_io import GlbData, read_glb


_MATERIAL_TEXTURE_SLOTS = (
    ("pbrMetallicRoughness", "baseColorTexture"),
    ("pbrMetallicRoughness", "metallicRoughnessTexture"),
    (None, "normalTexture"),
    (None, "occlusionTexture"),
    (None, "emissiveTexture"),
)

_COMPONENT_FORMATS = {
    5120: ("b", 1),
    5121: ("B", 1),
    5122: ("h", 2),
    5123: ("H", 2),
    5125: ("I", 4),
    5126: ("f", 4),
}
_TYPE_WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

_IDENTITY_MATRIX = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


def interior_wall_cap_material_name(material_name: str) -> str:
    stem = re.sub(r"[^a-z0-9_-]+", "_", str(material_name).casefold()).strip("_") or "wall"
    return f"rae_interior_wall_top_black__{stem}"


@dataclass(frozen=True)
class MaterialComponent:
    material: str
    index: int
    mesh_index: int
    primitive_index: int
    triangle_count: int
    bounds_min: tuple[float, float, float]
    bounds_max: tuple[float, float, float]
    uv_span: tuple[float, float] = (0.0, 0.0)

    @property
    def extents(self) -> tuple[float, float, float]:
        return tuple(self.bounds_max[axis] - self.bounds_min[axis] for axis in range(3))

    @property
    def is_planar(self) -> bool:
        extents = self.extents
        return min(extents) <= max(0.01, max(extents) * 0.001)

    @property
    def repeat_patch_recommended(self) -> bool:
        return self.is_planar and max(self.uv_span) > 1.01


def suggest_spatial_tile_bounds(
    component: MaterialComponent,
    *,
    tile_size: float = 32.0,
) -> tuple[float, float, float, float]:
    """Choose one X/Z map cell anchored by a visible material component."""
    size = max(0.001, float(tile_size))

    def axis_bounds(axis: int) -> tuple[float, float]:
        minimum = float(component.bounds_min[axis])
        maximum = float(component.bounds_max[axis])
        extent = maximum - minimum
        if extent + 1e-5 >= size:
            lower = minimum
        else:
            lower = (minimum + maximum - size) / 2.0
        return lower, lower + size

    min_x, max_x = axis_bounds(0)
    min_z, max_z = axis_bounds(2)
    return (min_x, min_z, max_x, max_z)


def suggest_spatial_feature_bounds(
    component: MaterialComponent,
    *,
    padding: float = 8.0,
) -> tuple[float, float, float, float]:
    """Keep a complete multi-cell feature plus its adjoining edge layers."""
    margin = max(0.0, float(padding))
    return (
        float(component.bounds_min[0]) - margin,
        float(component.bounds_min[2]) - margin,
        float(component.bounds_max[0]) + margin,
        float(component.bounds_max[2]) + margin,
    )


def _logical_material_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def logical_tree_family(value: str) -> str | None:
    """Return the DS material family that forms one logical tree mesh."""
    compact = _logical_material_key(value)
    match = re.match(r"^(ki\d+|tree\d*|palm\d*|yasi\d*|yashi\d*)", compact)
    return match.group(1) if match else None


def is_shadow_material(value: str) -> bool:
    """Return whether a Nitro material is a projected object/map shadow."""
    compact = _logical_material_key(value)
    return "kage" in compact or "shadow" in compact


def is_puddle_material(value: str) -> bool:
    """Return whether a material is one of the bounded Gen 5 puddle pieces."""
    compact = _logical_material_key(value)
    return compact.startswith(("mizutama", "puddle"))


def is_shoreline_material(value: str) -> bool:
    """Return whether a material draws a bounded water/land seam."""
    compact = _logical_material_key(value)
    return (
        compact.startswith(("shore", "sore"))
        or compact.startswith("sea") and any(
            token in compact for token in ("simi", "zanami", "nami", "gake")
        )
    )


def is_water_material(value: str) -> bool:
    """Return whether a material can be one layer of a reusable water tile."""
    compact = _logical_material_key(value)
    return is_puddle_material(value) or is_shoreline_material(value) or compact.startswith(
        (
            "mizu",
            "kawa",
            "taki",
            "ike",
            "numa",
            "umi",
            "sea",
            "water",
            "pond",
            "pool",
            "marsh",
            "spmarsh",
        )
    )


def rock_material_family(value: str) -> str | None:
    """Return a conservative rock/cliff family without treating its floor as rock."""
    compact = _logical_material_key(value)
    if is_water_material(value):
        return None
    if "gake" in compact or compact.startswith("cliff"):
        return "cliff"
    if any(token in compact for token in ("iwa", "ishi", "rock", "stone")):
        return "rock"
    if compact.endswith("isi") or "michiisi" in compact:
        return "rock"
    return None


def tile_feature_kind(material_names: list[str] | tuple[str, ...]) -> str:
    """Classify the focused extractor row for safe companion-layer assembly."""
    names = [str(value) for value in material_names if str(value)]
    families = [logical_tile_family(value) for value in names]
    if any(str(family or "").startswith("tree:") for family in families):
        return "tree"
    if any(str(family or "").startswith("grass:") for family in families):
        return "grass"
    if any(str(family or "").startswith("puddle:") for family in families):
        return "water"
    if any(str(family or "").startswith("shore:") for family in families):
        return "shore"
    if any(is_waterfall_material(value) for value in names):
        return "waterfall"
    if any(is_shoreline_material(value) for value in names):
        return "shore"
    if any(is_water_material(value) for value in names):
        return "water"
    if any(rock_material_family(value) for value in names):
        return "rock"
    if names and all(is_shadow_material(value) for value in names):
        return "shadow"
    return "object"


def suggest_tile_surface_origin_y(
    material_components: dict[str, tuple[MaterialComponent, ...]],
    material_names: list[str] | tuple[str, ...],
) -> float | None:
    """Return the authored land/water surface used as Y=0 for layered tiles.

    Gen 5 ocean maps place the deep water plane below the map's land datum.
    Grounding the selected geometry by its minimum therefore lifts beaches by
    more than a tile. Prefer the map's ``sea_jimen`` datum when present; for a
    shoreline-only model the top of ``sea_simi`` is the same authored datum.
    Other feature types retain the normal lowest-point placement behavior.
    """
    kind = tile_feature_kind(material_names)
    if kind not in {"water", "shore", "waterfall"}:
        return None

    def components_for(predicate) -> list[MaterialComponent]:
        return [
            component
            for key, components in material_components.items()
            if predicate(_logical_material_key(key))
            for component in components
        ]

    floor = components_for(lambda key: key.startswith("seajimen"))
    if floor:
        return max(float(component.bounds_max[1]) for component in floor)
    seam = components_for(lambda key: key.startswith("sea") and "simi" in key)
    if seam:
        return max(float(component.bounds_max[1]) for component in seam)
    return None


def logical_tile_family(value: str) -> str | None:
    """Return a layered object family that should be one extractor row."""
    tree = logical_tree_family(value)
    if tree:
        return f"tree:{tree}"
    compact = _logical_material_key(value)
    if re.fullmatch(r"kusaec[123]s?", compact):
        return "grass:kusa_ec"
    if is_puddle_material(value):
        return "puddle:mizutama"
    if compact.startswith("sea") and any(token in compact for token in ("simi", "zanami")):
        return "shore:sea"
    if compact.startswith(("shore", "sore")):
        return f"shore:{compact}"
    return None


def is_composite_object_material(value: str) -> bool:
    """Return whether disconnected pieces normally form one reusable prop."""
    compact = _logical_material_key(value)
    return compact.startswith(
        (
            "doukutu",
            "cave",
            "fountain",
            "funshui",
            "windmill",
            "kazaguruma",
        )
    )


def cluster_spatial_components(
    components: tuple[MaterialComponent, ...] | list[MaterialComponent],
    *,
    gap: float = 4.0,
) -> tuple[MaterialComponent, ...]:
    """Join touching disconnected faces into complete prop occurrences.

    Nitro frequently stores the front, cap, opening, and trim of one cave or
    prop as disconnected islands under a single material.  Treating every
    island as a tile is what produced the apparently random "window" rows.
    """
    source = list(components)
    if len(source) < 2:
        return tuple(source)
    margin = max(0.0, float(gap))

    def near(left: MaterialComponent, right: MaterialComponent) -> bool:
        return not (
            left.bounds_max[0] < right.bounds_min[0] - margin
            or right.bounds_max[0] < left.bounds_min[0] - margin
            or left.bounds_max[2] < right.bounds_min[2] - margin
            or right.bounds_max[2] < left.bounds_min[2] - margin
        )

    pending = set(range(len(source)))
    groups: list[list[MaterialComponent]] = []
    while pending:
        seed = pending.pop()
        group = [source[seed]]
        frontier = [seed]
        while frontier:
            current = frontier.pop()
            joined = [index for index in pending if near(source[current], source[index])]
            for index in joined:
                pending.remove(index)
                frontier.append(index)
                group.append(source[index])
        groups.append(group)

    clustered: list[MaterialComponent] = []
    for index, group in enumerate(groups):
        first = group[0]
        clustered.append(
            MaterialComponent(
                material=first.material,
                index=index,
                mesh_index=first.mesh_index,
                primitive_index=first.primitive_index,
                triangle_count=sum(component.triangle_count for component in group),
                bounds_min=tuple(min(component.bounds_min[axis] for component in group) for axis in range(3)),
                bounds_max=tuple(max(component.bounds_max[axis] for component in group) for axis in range(3)),
                uv_span=(
                    max(component.uv_span[0] for component in group),
                    max(component.uv_span[1] for component in group),
                ),
            )
        )
    return tuple(sorted(clustered, key=lambda item: (item.bounds_min[2], item.bounds_min[0])))


def shoreline_tile_occurrences(
    components: tuple[MaterialComponent, ...] | list[MaterialComponent],
    *,
    tile_size: float = 16.0,
    depth_tiles: int = 3,
) -> tuple[MaterialComponent, ...]:
    """Build Gen 5 beach pieces as 1x3 straights and 3x3 corners."""
    cell = max(0.001, float(tile_size))
    depth = cell * max(1, int(depth_tiles))
    epsilon = max(1e-4, cell * 0.01)

    def aligned(value: float) -> bool:
        return abs(value / cell - round(value / cell)) <= epsilon / cell

    def depth_axis(minimum: float, maximum: float) -> tuple[float, float]:
        min_aligned = aligned(minimum)
        max_aligned = aligned(maximum)
        if min_aligned and not max_aligned:
            return minimum, minimum + depth
        if max_aligned and not min_aligned:
            return maximum - depth, maximum
        center = (minimum + maximum) / 2.0
        lower = math.floor(center / cell) * cell
        return lower - cell, lower - cell + depth

    occurrences: list[MaterialComponent] = []
    seen: set[tuple[float, float, float, float]] = set()

    def add(source: MaterialComponent, bounds: tuple[float, float, float, float]) -> None:
        key = tuple(round(value, 4) for value in bounds)
        if key in seen:
            return
        seen.add(key)
        min_x, min_z, max_x, max_z = bounds
        occurrences.append(
            MaterialComponent(
                material=source.material,
                index=len(occurrences),
                mesh_index=source.mesh_index,
                primitive_index=source.primitive_index,
                triangle_count=source.triangle_count,
                bounds_min=(min_x, source.bounds_min[1], min_z),
                bounds_max=(max_x, source.bounds_max[1], max_z),
                uv_span=source.uv_span,
            )
        )

    for component in components:
        extent_x, _extent_y, extent_z = component.extents
        long_axis = max(extent_x, extent_z)
        short_axis = min(extent_x, extent_z)
        if short_axis < cell * 1.45:
            # Tiny foam/trim islands are already represented by their larger
            # straight/corner component and must not become standalone tiles.
            continue
        if long_axis > depth + epsilon:
            if extent_x > extent_z:
                min_z, max_z = depth_axis(component.bounds_min[2], component.bounds_max[2])
                start = math.floor((component.bounds_min[0] + epsilon) / cell) * cell
                end = math.ceil((component.bounds_max[0] - epsilon) / cell) * cell
                cursor = start
                while cursor < end - epsilon:
                    add(component, (cursor, min_z, cursor + cell, max_z))
                    cursor += cell
            else:
                min_x, max_x = depth_axis(component.bounds_min[0], component.bounds_max[0])
                start = math.floor((component.bounds_min[2] + epsilon) / cell) * cell
                end = math.ceil((component.bounds_max[2] - epsilon) / cell) * cell
                cursor = start
                while cursor < end - epsilon:
                    add(component, (min_x, cursor, max_x, cursor + cell))
                    cursor += cell
            continue
        if long_axis <= depth + epsilon:
            min_x, max_x = depth_axis(component.bounds_min[0], component.bounds_max[0])
            min_z, max_z = depth_axis(component.bounds_min[2], component.bounds_max[2])
            add(component, (min_x, min_z, max_x, max_z))
    return tuple(occurrences or components)


def _component_spatial_repeat_size(
    component: MaterialComponent,
    *,
    default: float = 32.0,
) -> float:
    """Infer one logical X/Z repeat from geometry and its Nitro UV span."""
    extent_x, _extent_y, extent_z = component.extents
    span_u, span_v = component.uv_span
    mappings: list[tuple[float, float, float, float]] = []
    if extent_x > 1e-6 and extent_z > 1e-6 and span_u > 1e-6 and span_v > 1e-6:
        for mapped_x, mapped_z in ((span_u, span_v), (span_v, span_u)):
            size_x = extent_x / mapped_x
            size_z = extent_z / mapped_z
            similarity = abs(math.log(max(size_x, 1e-6) / max(size_z, 1e-6)))
            mappings.append((similarity, size_x, size_z, max(mapped_x, mapped_z)))
    if mappings:
        _score, size_x, size_z, max_span = min(mappings, key=lambda entry: entry[0])
        repeated_sizes = [
            size
            for size, extent, span in (
                (size_x, extent_x, extent_x / max(size_x, 1e-6)),
                (size_z, extent_z, extent_z / max(size_z, 1e-6)),
            )
            if span > 1.01 and size > 1e-6
        ]
        if repeated_sizes:
            return max(0.001, sum(repeated_sizes) / len(repeated_sizes))
        if max_span > 1.01:
            return max(0.001, min(size_x, size_z))
    horizontal_extent = max(extent_x, extent_z)
    repeated_spans = [span for span in (span_u, span_v) if span > 1.01]
    if horizontal_extent > 1e-6 and repeated_spans:
        return max(0.001, horizontal_extent / max(repeated_spans))
    if horizontal_extent > 1e-6 and max(span_u, span_v) > 1e-6:
        return max(0.001, horizontal_extent)
    return max(0.001, float(default))


def spatial_tile_occurrences(
    components: tuple[MaterialComponent, ...] | list[MaterialComponent],
    *,
    tile_size: float | None = None,
) -> tuple[MaterialComponent, ...]:
    """Expand connected strips/planes into navigable one-cell tile choices."""
    inferred_sizes = [
        _component_spatial_repeat_size(component)
        for component in components
        if max(component.uv_span) > 1.01
        and max(component.extents[0], component.extents[2]) > 1e-6
    ]
    if inferred_sizes and tile_size is None:
        frequency: dict[float, int] = defaultdict(int)
        for inferred in inferred_sizes:
            frequency[round(float(inferred), 4)] += 1
        shared_size = max(frequency, key=lambda value: (frequency[value], value))
    else:
        shared_size = None
    occurrences: list[MaterialComponent] = []
    for component in components:
        size = (
            max(0.001, float(tile_size))
            if tile_size is not None
            else shared_size or _component_spatial_repeat_size(component)
        )
        extent_x, _extent_y, extent_z = component.extents
        count_x = max(1, int(math.ceil(max(0.0, extent_x - 1e-5) / size)))
        count_z = max(1, int(math.ceil(max(0.0, extent_z - 1e-5) / size)))
        if (
            max(component.uv_span) <= 1e-6
            or (
                count_x == 1
                and count_z == 1
                and (extent_x <= 1e-5 or extent_x >= size - 1e-5)
                and (extent_z <= 1e-5 or extent_z >= size - 1e-5)
            )
        ):
            occurrences.append(component)
            continue
        start_x = float(component.bounds_min[0])
        start_z = float(component.bounds_min[2])
        if 1e-5 < extent_x < size - 1e-5:
            start_x = math.floor(start_x / size) * size
        if 1e-5 < extent_z < size - 1e-5:
            start_z = math.floor(start_z / size) * size
        for z_index in range(count_z):
            for x_index in range(count_x):
                min_x = start_x + x_index * size
                min_z = start_z + z_index * size
                max_x = min_x + size
                max_z = min_z + size
                occurrences.append(
                    MaterialComponent(
                        material=component.material,
                        index=len(occurrences),
                        mesh_index=component.mesh_index,
                        primitive_index=component.primitive_index,
                        triangle_count=component.triangle_count,
                        bounds_min=(min_x, component.bounds_min[1], min_z),
                        bounds_max=(max_x, component.bounds_max[1], max_z),
                        uv_span=component.uv_span,
                    )
                )
    return tuple(occurrences)


def is_waterfall_material(value: str) -> bool:
    """Return whether a Gen 5 material is one layer of a waterfall feature."""
    key = _logical_material_key(value)
    return key.startswith(("kawa01", "kawasoko", "takisakai", "takishibu"))


def is_waterfall_body_material(value: str) -> bool:
    return _logical_material_key(value).startswith("kawa01b")


def is_waterfall_adjoining_material(value: str) -> bool:
    """Wave/edge layers immediately above or below a Gen 5 waterfall."""
    key = _logical_material_key(value)
    return key.startswith(("kawanose", "kawafuchi", "kawakage"))


def suggest_logical_materials(material_names: list[str], focused: str) -> list[str]:
    """Return DS layers that form one logical tree, palm, or waterfall."""
    if is_waterfall_material(focused):
        return [
            name
            for name in material_names
            if is_waterfall_material(name) or is_waterfall_adjoining_material(name)
        ]
    family = logical_tree_family(focused)
    if not family:
        return [focused] if focused else []
    return [
        name
        for name in material_names
        if logical_tree_family(name) == family
    ]


def group_logical_tile_materials(
    material_names: list[str] | tuple[str, ...],
) -> tuple[tuple[str, ...], ...]:
    """Collapse the material layers of each DS tree into one extractor row."""
    groups: list[list[str]] = []
    group_index: dict[str, int] = {}
    seen: set[str] = set()
    for raw_name in material_names:
        name = str(raw_name or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        family = logical_tile_family(name)
        group_key = family or f"material:{key}"
        index = group_index.get(group_key)
        if index is None:
            group_index[group_key] = len(groups)
            groups.append([name])
        else:
            groups[index].append(name)
    return tuple(tuple(group) for group in groups)


def choose_logical_tile_anchor(
    material_components: dict[str, tuple[MaterialComponent, ...]],
    material_names: list[str] | tuple[str, ...],
) -> str:
    """Choose the lowest horizontal footprint layer for a batched DS object."""
    available = [
        (material, material_components.get(material.casefold(), ()))
        for material in material_names
        if material_components.get(material.casefold())
    ]
    if not available:
        return str(material_names[0]) if material_names else ""

    family = next((logical_tile_family(material) for material in material_names), None)
    if str(family or "").startswith("shore:"):
        # ``sea_simi`` owns the authoritative 16-unit coastline geometry;
        # ``sea_zanami`` is the animated foam layered over it.
        return min(
            available,
            key=lambda entry: (
                0 if "simi" in _logical_material_key(entry[0]) else 1,
                0 if "zanami" in _logical_material_key(entry[0]) else 1,
            ),
        )[0]

    def score(entry: tuple[str, tuple[MaterialComponent, ...]]) -> tuple[bool, float, int]:
        components = entry[1]
        horizontal = any(
            component.extents[1] <= 1e-4
            and component.extents[0] > 1e-4
            and component.extents[2] > 1e-4
            for component in components
        )
        centers_y = sorted(
            (component.bounds_min[1] + component.bounds_max[1]) / 2.0
            for component in components
        )
        return (not horizontal, centers_y[len(centers_y) // 2], -len(components))

    return min(available, key=score)[0]


def spatial_assembly_materials(
    material_components: dict[str, tuple[MaterialComponent, ...]],
    anchor_materials: list[str] | tuple[str, ...],
    spatial_bounds: tuple[float, float, float, float],
    anchor_y_bounds: tuple[float, float],
    *,
    layer_reach: float = 64.0,
    companion_reach: float = 16.0,
    feature_kind: str | None = None,
) -> tuple[str, ...]:
    """Find safe semantic companion layers in one inferred DS footprint.

    The previous proximity-only rule could turn a window or ``h_kage`` shadow
    into a building, bridge, trees, and pond simply because those materials
    overlapped in X/Z.  Layer assembly is now opt-in by feature type: trees and
    grass already declare their complete material family; water may collect
    water/reflection layers; cliffs may collect other cliff layers.  Terrain
    floors are never attached to props or loose rocks.
    """
    min_x, min_z, max_x, max_z = spatial_bounds

    def overlaps(component: MaterialComponent) -> bool:
        epsilon = 1e-4
        return not (
            component.bounds_max[0] <= min_x + epsilon
            or component.bounds_min[0] >= max_x - epsilon
            or component.bounds_max[2] <= min_z + epsilon
            or component.bounds_min[2] >= max_z - epsilon
        )

    def vertical_gap(component: MaterialComponent, minimum: float, maximum: float) -> float:
        if component.bounds_max[1] < minimum:
            return minimum - component.bounds_max[1]
        if component.bounds_min[1] > maximum:
            return component.bounds_min[1] - maximum
        return 0.0

    kind = str(feature_kind or tile_feature_kind(anchor_materials)).casefold()
    if kind in {"tree", "grass", "shadow", "object"}:
        return tuple(anchor_materials)
    anchor_min, anchor_max = anchor_y_bounds
    anchor_keys = {material.casefold() for material in anchor_materials}
    focused_layers = [
        component
        for key, components in material_components.items()
        if key.casefold() in anchor_keys
        for component in components
        if overlaps(component)
        and vertical_gap(component, anchor_min, anchor_max) <= layer_reach
    ]
    if focused_layers:
        layer_min = min(component.bounds_min[1] for component in focused_layers)
        layer_max = max(component.bounds_max[1] for component in focused_layers)
    else:
        layer_min, layer_max = anchor_min, anchor_max

    assembled = list(anchor_materials)
    seen = {material.casefold() for material in assembled}
    anchor_rock_families = {
        family for material in anchor_materials
        if (family := rock_material_family(material)) is not None
    }
    effective_companion_reach = (
        max(float(companion_reach), 128.0)
        if kind in {"water", "shore", "waterfall"}
        else float(companion_reach)
    )
    for key, components in material_components.items():
        normalized = key.casefold()
        if normalized in seen or not components:
            continue
        material_name = components[0].material
        if kind in {"water", "shore", "waterfall"}:
            if kind == "shore":
                # Coastline pieces are overlays around a separately placeable
                # water-body tile. Pulling in map-wide sea_mizu planes makes
                # every corner a solid gradient square and defeats the tile
                # grammar used by the DS maps.
                if not is_shoreline_material(material_name):
                    continue
            elif not is_water_material(material_name):
                continue
        elif kind == "rock":
            if rock_material_family(material_name) not in anchor_rock_families:
                continue
        else:
            continue
        if any(
            overlaps(component)
            and vertical_gap(component, layer_min, layer_max) <= effective_companion_reach
            for component in components
        ):
            assembled.append(components[0].material)
            seen.add(normalized)
    return tuple(assembled)


def _accessor_values(glb: GlbData, accessor_index: int) -> list[tuple[float, ...]]:
    accessors = glb.json.get("accessors") or []
    views = glb.json.get("bufferViews") or []
    if not (0 <= accessor_index < len(accessors)):
        return []
    accessor = accessors[accessor_index]
    if not isinstance(accessor, dict) or not isinstance(accessor.get("bufferView"), int):
        return []
    view = views[accessor["bufferView"]]
    component = _COMPONENT_FORMATS.get(int(accessor.get("componentType") or 0))
    width = _TYPE_WIDTHS.get(str(accessor.get("type") or ""))
    count = int(accessor.get("count") or 0)
    if not component or not width or count <= 0:
        return []
    fmt, component_size = component
    item_size = component_size * width
    stride = int(view.get("byteStride") or item_size)
    start = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    unpack = struct.Struct("<" + fmt * width)
    values = []
    for index in range(count):
        offset = start + index * stride
        if offset + item_size > len(glb.bin_chunk):
            break
        values.append(tuple(float(value) for value in unpack.unpack_from(glb.bin_chunk, offset)))
    return values


def _primitive_triangle_indices(glb: GlbData, primitive: dict) -> list[tuple[int, int, int]]:
    if int(primitive.get("mode", 4)) != 4:
        return []
    position_index = (primitive.get("attributes") or {}).get("POSITION")
    if not isinstance(position_index, int):
        return []
    positions = _accessor_values(glb, position_index)
    if isinstance(primitive.get("indices"), int):
        flat = [int(value[0]) for value in _accessor_values(glb, primitive["indices"]) if value]
    else:
        flat = list(range(len(positions)))
    return [tuple(flat[index : index + 3]) for index in range(0, len(flat) - 2, 3)]


def _vertical_face_triangles(
    glb: GlbData,
    primitive: dict,
    triangles: list[tuple[int, int, int]],
    *,
    max_vertical_normal: float = 0.35,
) -> list[tuple[int, int, int]]:
    """Keep lateral faces while dropping horizontal caps from wall geometry."""
    position_index = (primitive.get("attributes") or {}).get("POSITION")
    if not isinstance(position_index, int):
        return triangles
    positions = _accessor_values(glb, position_index)
    kept: list[tuple[int, int, int]] = []
    for triangle in triangles:
        if any(index < 0 or index >= len(positions) for index in triangle):
            continue
        a, b, c = (positions[index] for index in triangle)
        normal = _vector_cross(_vector_sub(b, a), _vector_sub(c, a))
        length = math.sqrt(sum(component * component for component in normal))
        if length <= 1e-8:
            continue
        if abs(normal[1]) <= max_vertical_normal * length:
            kept.append(triangle)
    return kept


def _upward_face_triangles(
    glb: GlbData,
    primitive: dict,
    triangles: list[tuple[int, int, int]],
    *,
    min_vertical_normal: float = 0.7,
) -> list[tuple[int, int, int]]:
    """Return upward wall caps so they can receive a neutral cutaway material."""
    position_index = (primitive.get("attributes") or {}).get("POSITION")
    if not isinstance(position_index, int):
        return []
    positions = _accessor_values(glb, position_index)
    kept: list[tuple[int, int, int]] = []
    for triangle in triangles:
        if any(index < 0 or index >= len(positions) for index in triangle):
            continue
        a, b, c = (positions[index] for index in triangle)
        normal = _vector_cross(_vector_sub(b, a), _vector_sub(c, a))
        length = math.sqrt(sum(component * component for component in normal))
        if length > 1e-8 and normal[1] >= min_vertical_normal * length:
            kept.append(triangle)
    return kept


def _without_cutaway_edge_faces(
    glb: GlbData,
    primitive: dict,
    triangles: list[tuple[int, int, int]],
    edge: str,
    coordinate: float,
) -> list[tuple[int, int, int]]:
    """Remove wall faces occupying the entrance-side boundary tile band."""
    position_index = (primitive.get("attributes") or {}).get("POSITION")
    if not isinstance(position_index, int):
        return triangles
    positions = _accessor_values(glb, position_index)
    axis = 0 if edge in {"east", "west"} else 2
    positive = edge in {"east", "south"}
    kept: list[tuple[int, int, int]] = []
    for triangle in triangles:
        if any(index < 0 or index >= len(positions) for index in triangle):
            continue
        center = sum(float(positions[index][axis]) for index in triangle) / 3.0
        outside = center >= coordinate - 1e-4 if positive else center <= coordinate + 1e-4
        if not outside:
            kept.append(triangle)
    return kept


def _triangle_components(triangles: list[tuple[int, int, int]]) -> list[list[tuple[int, int, int]]]:
    by_vertex: dict[int, list[int]] = defaultdict(list)
    for triangle_index, triangle in enumerate(triangles):
        for vertex in triangle:
            by_vertex[vertex].append(triangle_index)
    seen: set[int] = set()
    components: list[list[tuple[int, int, int]]] = []
    for start in range(len(triangles)):
        if start in seen:
            continue
        seen.add(start)
        pending = [start]
        component: list[tuple[int, int, int]] = []
        while pending:
            current = pending.pop()
            triangle = triangles[current]
            component.append(triangle)
            for vertex in triangle:
                for neighbor in by_vertex[vertex]:
                    if neighbor not in seen:
                        seen.add(neighbor)
                        pending.append(neighbor)
        components.append(component)
    return components


def list_material_components(source: Path) -> dict[str, tuple[MaterialComponent, ...]]:
    """List disconnected instances for every material without importing trimesh."""
    glb = read_glb(source)
    materials = list(glb.json.get("materials") or [])
    names = {
        index: str(material.get("name") or f"material_{index}").strip()
        for index, material in enumerate(materials)
        if isinstance(material, dict)
    }
    result: dict[str, list[MaterialComponent]] = defaultdict(list)
    for mesh_index, mesh in enumerate(glb.json.get("meshes") or []):
        if not isinstance(mesh, dict):
            continue
        for primitive_index, primitive in enumerate(mesh.get("primitives") or []):
            if not isinstance(primitive, dict):
                continue
            material_index = primitive.get("material")
            if not isinstance(material_index, int):
                continue
            material = names.get(material_index, f"material_{material_index}")
            position_index = (primitive.get("attributes") or {}).get("POSITION")
            uv_index = (primitive.get("attributes") or {}).get("TEXCOORD_0")
            positions = _accessor_values(glb, position_index) if isinstance(position_index, int) else []
            uvs = _accessor_values(glb, uv_index) if isinstance(uv_index, int) else []
            components = _triangle_components(_primitive_triangle_indices(glb, primitive))
            if not components and positions:
                components = [[tuple(range(min(3, len(positions))))]]
            for triangles in components:
                used = {
                    vertex for triangle in triangles for vertex in triangle
                    if 0 <= vertex < len(positions) and len(positions[vertex]) >= 3
                }
                if not used:
                    continue
                values = [positions[vertex] for vertex in used]
                minimum = tuple(min(value[axis] for value in values) for axis in range(3))
                maximum = tuple(max(value[axis] for value in values) for axis in range(3))
                component_uvs = [uvs[vertex] for vertex in used if vertex < len(uvs) and len(uvs[vertex]) >= 2]
                uv_span = (
                    tuple(
                        max(value[axis] for value in component_uvs) - min(value[axis] for value in component_uvs)
                        for axis in range(2)
                    )
                    if component_uvs
                    else (0.0, 0.0)
                )
                result[material].append(
                    MaterialComponent(
                        material=material,
                        index=len(result[material]),
                        mesh_index=mesh_index,
                        primitive_index=primitive_index,
                        triangle_count=len(triangles),
                        bounds_min=minimum,
                        bounds_max=maximum,
                        uv_span=uv_span,
                    )
                )
    return {name: tuple(components) for name, components in result.items()}


def _matrix_multiply(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, ...]:
    """Multiply two glTF column-major 4x4 matrices."""
    return tuple(
        sum(left[k * 4 + row] * right[column * 4 + k] for k in range(4))
        for column in range(4)
        for row in range(4)
    )


def _node_matrix(node: dict) -> tuple[float, ...]:
    matrix = node.get("matrix")
    if isinstance(matrix, list) and len(matrix) == 16:
        try:
            return tuple(float(value) for value in matrix)
        except (TypeError, ValueError):
            pass
    translation = node.get("translation") or (0.0, 0.0, 0.0)
    scale = node.get("scale") or (1.0, 1.0, 1.0)
    rotation = node.get("rotation") or (0.0, 0.0, 0.0, 1.0)
    try:
        tx, ty, tz = (float(translation[index]) for index in range(3))
        sx, sy, sz = (float(scale[index]) for index in range(3))
        x, y, z, w = (float(rotation[index]) for index in range(4))
    except (IndexError, TypeError, ValueError):
        return _IDENTITY_MATRIX
    length = math.sqrt(x * x + y * y + z * z + w * w)
    if length > 0:
        x, y, z, w = (value / length for value in (x, y, z, w))
    else:
        x = y = z = 0.0
        w = 1.0
    r00 = 1 - 2 * (y * y + z * z)
    r01 = 2 * (x * y - z * w)
    r02 = 2 * (x * z + y * w)
    r10 = 2 * (x * y + z * w)
    r11 = 1 - 2 * (x * x + z * z)
    r12 = 2 * (y * z - x * w)
    r20 = 2 * (x * z - y * w)
    r21 = 2 * (y * z + x * w)
    r22 = 1 - 2 * (x * x + y * y)
    return (
        r00 * sx, r10 * sx, r20 * sx, 0.0,
        r01 * sy, r11 * sy, r21 * sy, 0.0,
        r02 * sz, r12 * sz, r22 * sz, 0.0,
        tx, ty, tz, 1.0,
    )


def _transform_position(matrix: tuple[float, ...], value: tuple[float, ...]) -> tuple[float, float, float]:
    x, y, z = value[:3]
    return (
        matrix[0] * x + matrix[4] * y + matrix[8] * z + matrix[12],
        matrix[1] * x + matrix[5] * y + matrix[9] * z + matrix[13],
        matrix[2] * x + matrix[6] * y + matrix[10] * z + matrix[14],
    )


def _primitive_positions(glb: GlbData, mesh_index: int) -> list[tuple[float, ...]]:
    meshes = glb.json.get("meshes") or []
    if not (0 <= mesh_index < len(meshes)) or not isinstance(meshes[mesh_index], dict):
        return []
    positions: list[tuple[float, ...]] = []
    for primitive in meshes[mesh_index].get("primitives") or []:
        if not isinstance(primitive, dict):
            continue
        position_index = (primitive.get("attributes") or {}).get("POSITION")
        if not isinstance(position_index, int):
            continue
        source_positions = _accessor_values(glb, position_index)
        if isinstance(primitive.get("indices"), int):
            indices = _accessor_values(glb, primitive["indices"])
            used = [int(value[0]) for value in indices if value]
            positions.extend(
                source_positions[index]
                for index in used
                if 0 <= index < len(source_positions) and len(source_positions[index]) >= 3
            )
        else:
            positions.extend(value for value in source_positions if len(value) >= 3)
    return positions


def _selected_world_positions(glb: GlbData) -> list[tuple[float, float, float]]:
    nodes = glb.json.get("nodes") or []
    scenes = glb.json.get("scenes") or []
    scene_index = int(glb.json.get("scene") or 0)
    roots = []
    if 0 <= scene_index < len(scenes) and isinstance(scenes[scene_index], dict):
        roots = [value for value in (scenes[scene_index].get("nodes") or []) if isinstance(value, int)]
    if not roots:
        child_indices = {
            child
            for node in nodes
            if isinstance(node, dict)
            for child in (node.get("children") or [])
            if isinstance(child, int)
        }
        roots = [index for index in range(len(nodes)) if index not in child_indices]

    positions: list[tuple[float, float, float]] = []

    def visit(node_index: int, parent_matrix: tuple[float, ...], ancestors: frozenset[int]) -> None:
        if node_index in ancestors or not (0 <= node_index < len(nodes)):
            return
        node = nodes[node_index]
        if not isinstance(node, dict):
            return
        world_matrix = _matrix_multiply(parent_matrix, _node_matrix(node))
        mesh_index = node.get("mesh")
        if isinstance(mesh_index, int):
            positions.extend(
                _transform_position(world_matrix, value)
                for value in _primitive_positions(glb, mesh_index)
            )
        next_ancestors = ancestors | {node_index}
        for child in node.get("children") or []:
            if isinstance(child, int):
                visit(child, world_matrix, next_ancestors)

    for root in roots:
        visit(root, _IDENTITY_MATRIX, frozenset())

    if positions:
        return positions
    for mesh_index in range(len(glb.json.get("meshes") or [])):
        positions.extend(
            _transform_position(_IDENTITY_MATRIX, value)
            for value in _primitive_positions(glb, mesh_index)
        )
    return positions


def _recenter_selected_geometry(glb: GlbData, *, origin_y: float | None = None) -> None:
    positions = _selected_world_positions(glb)
    if not positions:
        return
    minimum = [min(value[axis] for value in positions) for axis in range(3)]
    maximum = [max(value[axis] for value in positions) for axis in range(3)]
    center_x = (minimum[0] + maximum[0]) / 2.0
    center_z = (minimum[2] + maximum[2]) / 2.0
    anchor_y = minimum[1] if origin_y is None else float(origin_y)
    translation = [-center_x, -anchor_y, -center_z]
    scenes = glb.json.setdefault("scenes", [{"nodes": []}])
    scene_index = int(glb.json.get("scene") or 0)
    if not (0 <= scene_index < len(scenes)):
        scene_index = 0
        glb.json["scene"] = 0
    roots = [int(value) for value in (scenes[scene_index].get("nodes") or []) if isinstance(value, int)]
    nodes = glb.json.setdefault("nodes", [])
    parent_index = len(nodes)
    nodes.append({"name": "rae_tile_origin", "translation": translation, "children": roots})
    scenes[scene_index]["nodes"] = [parent_index]
    glb.json.setdefault("extras", {}).setdefault("rae", {})["tileBounds"] = {
        "min": minimum,
        "max": maximum,
        "originTranslation": translation,
        **({"originY": anchor_y} if origin_y is not None else {}),
    }


def recenter_glb_geometry(source: Path, output: Path) -> Path:
    """Center a complete GLB on X/Z and place its lowest point on Y=0.

    The correction is stored as a scene parent so skins, animation targets,
    material-motion metadata, and the original vertex buffers remain intact.
    This is the placement-ready path used by NDS building and tile exports.
    """
    source_glb = read_glb(source)
    result = GlbData(
        json=copy.deepcopy(source_glb.json),
        bin_chunk=source_glb.bin_chunk,
    )
    _recenter_selected_geometry(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    result.write(output)
    return output


def _prune_material_images(gltf: dict) -> None:
    """Compact texture/image tables to dependencies of the kept materials."""
    texture_indices: set[int] = set()
    texture_infos: list[dict] = []
    for material in gltf.get("materials") or []:
        if not isinstance(material, dict):
            continue
        for parent_name, slot_name in _MATERIAL_TEXTURE_SLOTS:
            parent = material.get(parent_name) if parent_name else material
            info = parent.get(slot_name) if isinstance(parent, dict) else None
            if not isinstance(info, dict) or not isinstance(info.get("index"), int):
                continue
            texture_indices.add(info["index"])
            texture_infos.append(info)

    textures = list(gltf.get("textures") or [])
    valid_texture_indices = sorted(index for index in texture_indices if 0 <= index < len(textures))
    texture_remap = {old: new for new, old in enumerate(valid_texture_indices)}
    for info in texture_infos:
        if info["index"] in texture_remap:
            info["index"] = texture_remap[info["index"]]

    kept_textures = [textures[index] for index in valid_texture_indices]
    image_indices: set[int] = set()
    for texture in kept_textures:
        if not isinstance(texture, dict):
            continue
        source = texture.get("source")
        if isinstance(source, int):
            image_indices.add(source)
        basisu = (texture.get("extensions") or {}).get("KHR_texture_basisu")
        if isinstance(basisu, dict) and isinstance(basisu.get("source"), int):
            image_indices.add(basisu["source"])

    images = list(gltf.get("images") or [])
    valid_image_indices = sorted(index for index in image_indices if 0 <= index < len(images))
    image_remap = {old: new for new, old in enumerate(valid_image_indices)}
    for texture in kept_textures:
        if not isinstance(texture, dict):
            continue
        if texture.get("source") in image_remap:
            texture["source"] = image_remap[texture["source"]]
        basisu = (texture.get("extensions") or {}).get("KHR_texture_basisu")
        if isinstance(basisu, dict) and basisu.get("source") in image_remap:
            basisu["source"] = image_remap[basisu["source"]]

    if "textures" in gltf:
        gltf["textures"] = kept_textures
    if "images" in gltf:
        gltf["images"] = [images[index] for index in valid_image_indices]


def _prune_material_motion(gltf: dict, selected: set[str]) -> None:
    rae = (gltf.get("extras") or {}).get("rae") or {}
    motion = rae.get("mapMaterialMotion")
    if not isinstance(motion, dict):
        return
    clips = []
    for clip in motion.get("clips") or []:
        if not isinstance(clip, dict):
            continue
        tracks = [
            track for track in (clip.get("tracks") or [])
            if isinstance(track, dict) and str(track.get("material") or "").casefold() in selected
        ]
        if tracks:
            kept = copy.deepcopy(clip)
            kept["tracks"] = tracks
            clips.append(kept)
    if not clips:
        rae.pop("mapMaterialMotion", None)
        return
    motion["clips"] = clips
    known = {str(clip.get("id") or "") for clip in clips}
    if str(motion.get("defaultClip") or "") not in known:
        motion["defaultClip"] = str(clips[0].get("id") or "")


def _append_accessor(
    gltf: dict,
    bin_chunk: bytearray,
    values: list[tuple[float | int, ...]],
    *,
    component_type: int,
    value_type: str,
    normalized: bool = False,
) -> int:
    component = _COMPONENT_FORMATS[component_type]
    fmt, _size = component
    while len(bin_chunk) % 4:
        bin_chunk.append(0)
    start = len(bin_chunk)
    packer = struct.Struct("<" + fmt * _TYPE_WIDTHS[value_type])
    for value in values:
        bin_chunk.extend(packer.pack(*value))
    views = gltf.setdefault("bufferViews", [])
    view_index = len(views)
    views.append({"buffer": 0, "byteOffset": start, "byteLength": len(bin_chunk) - start})
    accessor: dict = {
        "bufferView": view_index,
        "componentType": component_type,
        "count": len(values),
        "type": value_type,
    }
    if normalized:
        accessor["normalized"] = True
    if values and value_type in {"VEC2", "VEC3"} and component_type == 5126:
        width = _TYPE_WIDTHS[value_type]
        accessor["min"] = [min(float(value[axis]) for value in values) for axis in range(width)]
        accessor["max"] = [max(float(value[axis]) for value in values) for axis in range(width)]
    accessors = gltf.setdefault("accessors", [])
    accessor_index = len(accessors)
    accessors.append(accessor)
    return accessor_index


def _vector_cross(left: tuple[float, float, float], right: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _vector_sub(left: tuple[float, ...], right: tuple[float, ...]) -> tuple[float, float, float]:
    return tuple(float(left[axis]) - float(right[axis]) for axis in range(3))


def _component_position_bounds(
    glb: GlbData,
    primitive: dict,
    component: list[tuple[int, int, int]],
) -> tuple[tuple[float, float, float], tuple[float, float, float]] | None:
    position_index = (primitive.get("attributes") or {}).get("POSITION")
    if not isinstance(position_index, int):
        return None
    positions = _accessor_values(glb, position_index)
    vertices = {
        vertex
        for triangle in component
        for vertex in triangle
        if 0 <= vertex < len(positions) and len(positions[vertex]) >= 3
    }
    if not vertices:
        return None
    minimum = tuple(min(positions[index][axis] for index in vertices) for axis in range(3))
    maximum = tuple(max(positions[index][axis] for index in vertices) for axis in range(3))
    return minimum, maximum


def _component_intersects_spatial_tile(
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    spatial_tile_bounds: tuple[float, float, float, float],
) -> bool:
    if bounds is None:
        return False
    minimum, maximum = bounds
    min_x, min_z, max_x, max_z = spatial_tile_bounds
    epsilon = 1e-4
    return not (
        maximum[0] <= min_x + epsilon
        or minimum[0] >= max_x - epsilon
        or maximum[2] <= min_z + epsilon
        or minimum[2] >= max_z - epsilon
    )


def _component_center_in_spatial_tile(
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    spatial_tile_bounds: tuple[float, float, float, float],
) -> bool:
    if bounds is None:
        return False
    minimum, maximum = bounds
    center_x = (minimum[0] + maximum[0]) / 2.0
    center_z = (minimum[2] + maximum[2]) / 2.0
    min_x, min_z, max_x, max_z = spatial_tile_bounds
    epsilon = 1e-4
    return (
        min_x - epsilon <= center_x <= max_x + epsilon
        and min_z - epsilon <= center_z <= max_z + epsilon
    )


def _component_crosses_spatial_tile(
    bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None,
    spatial_tile_bounds: tuple[float, float, float, float],
) -> bool:
    if bounds is None:
        return False
    minimum, maximum = bounds
    min_x, min_z, max_x, max_z = spatial_tile_bounds
    epsilon = 1e-4
    return (
        minimum[0] < min_x - epsilon
        or maximum[0] > max_x + epsilon
        or minimum[2] < min_z - epsilon
        or maximum[2] > max_z + epsilon
    )


def _replace_with_component_geometry(
    source_glb: GlbData,
    gltf: dict,
    bin_chunk: bytearray,
    primitive: dict,
    component: list[tuple[int, int, int]],
) -> bool:
    """Compact a disconnected component so viewer bounds contain only that tile."""
    vertices = sorted({vertex for triangle in component for vertex in triangle})
    if not vertices:
        return False
    source_attributes = primitive.get("attributes") or {}
    compact_attributes: dict[str, int] = {}
    for semantic, accessor_index in source_attributes.items():
        if not isinstance(accessor_index, int):
            continue
        try:
            accessor = source_glb.json["accessors"][accessor_index]
        except (IndexError, KeyError, TypeError):
            continue
        component_type = int(accessor.get("componentType") or 0)
        value_type = str(accessor.get("type") or "")
        if component_type not in _COMPONENT_FORMATS or value_type not in _TYPE_WIDTHS:
            continue
        source_values = _accessor_values(source_glb, accessor_index)
        if any(vertex >= len(source_values) for vertex in vertices):
            return False
        values = [source_values[vertex] for vertex in vertices]
        if component_type != 5126:
            values = [tuple(int(round(value)) for value in row) for row in values]
        compact_attributes[str(semantic)] = _append_accessor(
            gltf,
            bin_chunk,
            values,
            component_type=component_type,
            value_type=value_type,
            normalized=bool(accessor.get("normalized")),
        )
    if "POSITION" not in compact_attributes:
        return False
    remap = {source_vertex: index for index, source_vertex in enumerate(vertices)}
    compact_indices = [remap[vertex] for triangle in component for vertex in triangle]
    primitive["attributes"] = compact_attributes
    primitive["indices"] = _append_accessor(
        gltf,
        bin_chunk,
        [(value,) for value in compact_indices],
        component_type=5125 if len(vertices) > 65535 else 5123,
        value_type="SCALAR",
    )
    primitive["mode"] = 4
    extensions = dict(primitive.get("extensions") or {})
    extensions.pop("FB_ngon_encoding", None)
    if extensions:
        primitive["extensions"] = extensions
    else:
        primitive.pop("extensions", None)
    return True


def _replace_with_spatially_clipped_geometry(
    source_glb: GlbData,
    gltf: dict,
    bin_chunk: bytearray,
    primitive: dict,
    component: list[tuple[int, int, int]],
    spatial_tile_bounds: tuple[float, float, float, float],
) -> bool:
    """Clip original triangles to X/Z bounds without filling shoreline holes."""
    source_attributes = primitive.get("attributes") or {}
    attribute_specs: dict[str, tuple[int, str, bool, list[tuple[float, ...]]]] = {}
    for semantic, accessor_index in source_attributes.items():
        if not isinstance(accessor_index, int):
            continue
        try:
            accessor = source_glb.json["accessors"][accessor_index]
        except (IndexError, KeyError, TypeError):
            continue
        component_type = int(accessor.get("componentType") or 0)
        value_type = str(accessor.get("type") or "")
        if component_type not in _COMPONENT_FORMATS or value_type not in _TYPE_WIDTHS:
            continue
        attribute_specs[str(semantic)] = (
            component_type,
            value_type,
            bool(accessor.get("normalized")),
            _accessor_values(source_glb, accessor_index),
        )
    position_spec = attribute_specs.get("POSITION")
    if position_spec is None:
        return False
    used_vertices = {vertex for triangle in component for vertex in triangle}
    attribute_specs = {
        semantic: spec
        for semantic, spec in attribute_specs.items()
        if all(0 <= vertex < len(spec[3]) for vertex in used_vertices)
    }
    position_spec = attribute_specs.get("POSITION")
    if position_spec is None:
        return False
    position_values = position_spec[3]

    def source_vertex(index: int) -> dict[str, tuple[float, ...]] | None:
        if index < 0 or index >= len(position_values):
            return None
        result: dict[str, tuple[float, ...]] = {}
        for semantic, (_component_type, _value_type, _normalized, values) in attribute_specs.items():
            if index >= len(values):
                continue
            result[semantic] = tuple(float(value) for value in values[index])
        return result if "POSITION" in result else None

    def interpolate(
        left: dict[str, tuple[float, ...]],
        right: dict[str, tuple[float, ...]],
        amount: float,
    ) -> dict[str, tuple[float, ...]]:
        result: dict[str, tuple[float, ...]] = {}
        for semantic in left.keys() & right.keys():
            first = left[semantic]
            second = right[semantic]
            if semantic.startswith("JOINTS_"):
                result[semantic] = first if amount < 0.5 else second
                continue
            result[semantic] = tuple(
                first[index] + (second[index] - first[index]) * amount
                for index in range(min(len(first), len(second)))
            )
        return result

    def clip_plane(
        polygon: list[dict[str, tuple[float, ...]]],
        *,
        axis: int,
        boundary: float,
        keep_greater: bool,
    ) -> list[dict[str, tuple[float, ...]]]:
        if not polygon:
            return []

        def inside(vertex: dict[str, tuple[float, ...]]) -> bool:
            value = vertex["POSITION"][axis]
            return value >= boundary - 1e-5 if keep_greater else value <= boundary + 1e-5

        clipped: list[dict[str, tuple[float, ...]]] = []
        previous = polygon[-1]
        previous_inside = inside(previous)
        for current in polygon:
            current_inside = inside(current)
            if current_inside != previous_inside:
                first = previous["POSITION"][axis]
                second = current["POSITION"][axis]
                denominator = second - first
                amount = 0.0 if abs(denominator) <= 1e-8 else (boundary - first) / denominator
                clipped.append(interpolate(previous, current, max(0.0, min(1.0, amount))))
            if current_inside:
                clipped.append(current)
            previous = current
            previous_inside = current_inside
        return clipped

    min_x, min_z, max_x, max_z = spatial_tile_bounds
    output_values: dict[str, list[tuple[float | int, ...]]] = {
        semantic: [] for semantic in attribute_specs
    }
    output_indices: list[int] = []
    vertex_cache: dict[tuple[tuple[str, tuple[float | int, ...]], ...], int] = {}
    for triangle in component:
        polygon = [source_vertex(vertex) for vertex in triangle]
        if any(vertex is None for vertex in polygon):
            continue
        clipped = [vertex for vertex in polygon if vertex is not None]
        for axis, boundary, keep_greater in (
            (0, min_x, True),
            (0, max_x, False),
            (2, min_z, True),
            (2, max_z, False),
        ):
            clipped = clip_plane(
                clipped,
                axis=axis,
                boundary=float(boundary),
                keep_greater=keep_greater,
            )
            if len(clipped) < 3:
                break
        if len(clipped) < 3:
            continue
        polygon_indices: list[int] = []
        for vertex in clipped:
            converted: dict[str, tuple[float | int, ...]] = {}
            for semantic, (component_type, _value_type, _normalized, _source) in attribute_specs.items():
                values = vertex[semantic]
                if component_type != 5126:
                    converted[semantic] = tuple(int(round(value)) for value in values)
                else:
                    converted[semantic] = tuple(float(value) for value in values)
            cache_key = tuple(
                (
                    semantic,
                    tuple(
                        round(float(value), 7) if isinstance(value, float) else value
                        for value in converted[semantic]
                    ),
                )
                for semantic in attribute_specs
            )
            vertex_index = vertex_cache.get(cache_key)
            if vertex_index is None:
                vertex_index = len(output_values["POSITION"])
                vertex_cache[cache_key] = vertex_index
                for semantic in attribute_specs:
                    output_values[semantic].append(converted[semantic])
            polygon_indices.append(vertex_index)
        for offset in range(1, len(polygon_indices) - 1):
            triangle = (
                polygon_indices[0],
                polygon_indices[offset],
                polygon_indices[offset + 1],
            )
            if len(set(triangle)) == 3:
                output_indices.extend(triangle)

    if not output_indices or not output_values["POSITION"]:
        return False
    compact_attributes: dict[str, int] = {}
    for semantic, (component_type, value_type, normalized, _source) in attribute_specs.items():
        values = output_values[semantic]
        if len(values) != len(output_values["POSITION"]):
            continue
        compact_attributes[semantic] = _append_accessor(
            gltf,
            bin_chunk,
            values,
            component_type=component_type,
            value_type=value_type,
            normalized=normalized,
        )
    if "POSITION" not in compact_attributes:
        return False
    primitive["attributes"] = compact_attributes
    primitive["indices"] = _append_accessor(
        gltf,
        bin_chunk,
        [(value,) for value in output_indices],
        component_type=5125 if len(output_values["POSITION"]) > 65535 else 5123,
        value_type="SCALAR",
    )
    primitive["mode"] = 4
    extensions = dict(primitive.get("extensions") or {})
    extensions.pop("FB_ngon_encoding", None)
    if extensions:
        primitive["extensions"] = extensions
    else:
        primitive.pop("extensions", None)
    return True


def _replace_with_repeat_patch(
    source_glb: GlbData,
    gltf: dict,
    bin_chunk: bytearray,
    primitive: dict,
    component: list[tuple[int, int, int]],
    *,
    patch_size: float,
    spatial_tile_bounds: tuple[float, float, float, float] | None = None,
) -> bool:
    attributes = primitive.get("attributes") or {}
    position_index = attributes.get("POSITION")
    uv_index = attributes.get("TEXCOORD_0")
    if not isinstance(position_index, int) or not isinstance(uv_index, int):
        return False
    positions = _accessor_values(source_glb, position_index)
    uvs = _accessor_values(source_glb, uv_index)
    vertices = sorted({vertex for triangle in component for vertex in triangle})
    if len(vertices) < 3 or any(vertex >= len(positions) or vertex >= len(uvs) for vertex in vertices):
        return False
    minimum = [min(positions[vertex][axis] for vertex in vertices) for axis in range(3)]
    maximum = [max(positions[vertex][axis] for vertex in vertices) for axis in range(3)]
    extents = [maximum[axis] - minimum[axis] for axis in range(3)]
    constant_axis = min(range(3), key=lambda axis: extents[axis])
    varying = [axis for axis in range(3) if axis != constant_axis]
    if extents[constant_axis] > max(0.01, max(extents) * 0.001):
        return False
    if min(extents[axis] for axis in varying) <= 0.001:
        return False

    basis = None
    for first in range(len(vertices) - 2):
        for second in range(first + 1, len(vertices) - 1):
            for third in range(second + 1, len(vertices)):
                p1 = positions[vertices[first]]
                p2 = positions[vertices[second]]
                p3 = positions[vertices[third]]
                x1, y1 = p1[varying[0]], p1[varying[1]]
                x2, y2 = p2[varying[0]], p2[varying[1]]
                x3, y3 = p3[varying[0]], p3[varying[1]]
                determinant = (x2 - x1) * (y3 - y1) - (y2 - y1) * (x3 - x1)
                if abs(determinant) > 1e-8:
                    basis = (vertices[first], vertices[second], vertices[third], determinant)
                    break
            if basis:
                break
        if basis:
            break
    if basis is None:
        return False

    first, second, third, determinant = basis
    p1, p2, p3 = positions[first], positions[second], positions[third]
    uv1, uv2, uv3 = uvs[first], uvs[second], uvs[third]

    def interpolate_uv(a: float, b: float) -> tuple[float, float]:
        x1, y1 = p1[varying[0]], p1[varying[1]]
        x2, y2 = p2[varying[0]], p2[varying[1]]
        x3, y3 = p3[varying[0]], p3[varying[1]]
        weight2 = ((a - x1) * (y3 - y1) - (b - y1) * (x3 - x1)) / determinant
        weight3 = ((x2 - x1) * (b - y1) - (y2 - y1) * (a - x1)) / determinant
        return tuple(
            float(uv1[axis])
            + weight2 * (float(uv2[axis]) - float(uv1[axis]))
            + weight3 * (float(uv3[axis]) - float(uv1[axis]))
            for axis in range(2)
        )

    origin_a, origin_b = minimum[varying[0]], minimum[varying[1]]
    if float(patch_size) > 0:
        size_a = min(float(patch_size), extents[varying[0]])
        size_b = min(float(patch_size), extents[varying[1]])
    else:
        uv_origin = interpolate_uv(origin_a, origin_b)
        uv_step_a = interpolate_uv(origin_a + 1.0, origin_b)
        uv_step_b = interpolate_uv(origin_a, origin_b + 1.0)
        rate_a = max(abs(uv_step_a[axis] - uv_origin[axis]) for axis in range(2))
        rate_b = max(abs(uv_step_b[axis] - uv_origin[axis]) for axis in range(2))
        size_a = min((1.0 / rate_a) if rate_a > 1e-8 else extents[varying[0]], extents[varying[0]])
        size_b = min((1.0 / rate_b) if rate_b > 1e-8 else extents[varying[1]], extents[varying[1]])
    if spatial_tile_bounds is not None:
        min_x, min_z, max_x, max_z = spatial_tile_bounds
        limits = {0: (min_x, max_x), 2: (min_z, max_z)}
        origins = [origin_a, origin_b]
        sizes = [size_a, size_b]
        for position, axis in enumerate(varying):
            if axis not in limits:
                continue
            lower, upper = limits[axis]
            clipped_lower = max(float(minimum[axis]), float(lower))
            clipped_upper = min(float(maximum[axis]), float(upper))
            if clipped_upper - clipped_lower <= 0.001:
                return False
            origins[position] = clipped_lower
            sizes[position] = clipped_upper - clipped_lower
        origin_a, origin_b = origins
        size_a, size_b = sizes
    coordinates = [
        (origin_a, origin_b),
        (origin_a, origin_b + size_b),
        (origin_a + size_a, origin_b + size_b),
        (origin_a + size_a, origin_b),
    ]
    fixed = sum(positions[vertex][constant_axis] for vertex in vertices) / len(vertices)
    patch_positions: list[tuple[float, float, float]] = []
    for a, b in coordinates:
        value = [0.0, 0.0, 0.0]
        value[constant_axis] = fixed
        value[varying[0]] = a
        value[varying[1]] = b
        patch_positions.append(tuple(value))

    patch_uvs = [interpolate_uv(a, b) for a, b in coordinates]
    source_triangle = component[0]
    source_normal = _vector_cross(
        _vector_sub(positions[source_triangle[1]], positions[source_triangle[0]]),
        _vector_sub(positions[source_triangle[2]], positions[source_triangle[0]]),
    )
    normal_length = math.sqrt(sum(value * value for value in source_normal)) or 1.0
    source_normal = tuple(value / normal_length for value in source_normal)
    patch_normal = _vector_cross(
        _vector_sub(patch_positions[1], patch_positions[0]),
        _vector_sub(patch_positions[2], patch_positions[0]),
    )
    same_winding = sum(patch_normal[axis] * source_normal[axis] for axis in range(3)) >= 0
    indices = (0, 1, 2, 0, 2, 3) if same_winding else (0, 2, 1, 0, 3, 2)

    primitive["attributes"] = {
        "POSITION": _append_accessor(gltf, bin_chunk, patch_positions, component_type=5126, value_type="VEC3"),
        "TEXCOORD_0": _append_accessor(gltf, bin_chunk, patch_uvs, component_type=5126, value_type="VEC2"),
        "NORMAL": _append_accessor(
            gltf,
            bin_chunk,
            [source_normal] * 4,
            component_type=5126,
            value_type="VEC3",
        ),
        "COLOR_0": _append_accessor(
            gltf,
            bin_chunk,
            [(255, 255, 255)] * 4,
            component_type=5121,
            value_type="VEC3",
            normalized=True,
        ),
        "JOINTS_0": _append_accessor(
            gltf,
            bin_chunk,
            [(0, 0, 0, 0)] * 4,
            component_type=5121,
            value_type="VEC4",
        ),
        "WEIGHTS_0": _append_accessor(
            gltf,
            bin_chunk,
            [(255, 0, 0, 0)] * 4,
            component_type=5121,
            value_type="VEC4",
            normalized=True,
        ),
    }
    primitive["indices"] = _append_accessor(
        gltf,
        bin_chunk,
        [(value,) for value in indices],
        component_type=5123,
        value_type="SCALAR",
    )
    primitive["mode"] = 4
    extensions = dict(primitive.get("extensions") or {})
    extensions.pop("FB_ngon_encoding", None)
    if extensions:
        primitive["extensions"] = extensions
    else:
        primitive.pop("extensions", None)
    return True


def extract_material_primitives(
    source: Path,
    output: Path,
    material_names: list[str] | tuple[str, ...] | set[str],
    *,
    recenter: bool = False,
    component_indices: dict[str, int] | None = None,
    repeat_patch_materials: set[str] | None = None,
    patch_size: float = 0.0,
    spatial_tile_bounds: tuple[float, float, float, float] | None = None,
    preserve_spatial_components: bool = False,
    spatial_component_center_filter: bool = False,
    origin_y: float | None = None,
    vertical_faces_only_materials: set[str] | None = None,
    black_wall_cap_materials: set[str] | None = None,
    cutaway_edge: str | None = None,
    cutaway_coordinate: float | None = None,
) -> Path:
    """Keep only primitives whose glTF material names were selected.

    Mesh and node indices stay stable so animation/node references remain valid.
    The material table is compacted because RTPKS imports one runtime material
    for every material declared by the GLB.
    """
    selected = {str(name).strip().casefold() for name in material_names if str(name).strip()}
    if not selected:
        raise ValueError("Select at least one material to extract.")

    glb = read_glb(source)
    gltf = copy.deepcopy(glb.json)
    bin_chunk = bytearray(glb.bin_chunk)
    component_targets = {
        str(name).casefold(): max(0, int(index))
        for name, index in (component_indices or {}).items()
    }
    repeat_patches = {str(name).casefold() for name in (repeat_patch_materials or set())}
    vertical_faces_only = {
        str(name).strip().casefold()
        for name in (vertical_faces_only_materials or set())
        if str(name).strip()
    }
    black_wall_caps = {
        str(name).strip().casefold()
        for name in (black_wall_cap_materials or set())
        if str(name).strip()
    }
    materials = list(gltf.get("materials") or [])
    black_cap_material_indices: dict[str, int] = {}
    for wall_material in sorted(black_wall_caps):
        black_cap_material_indices[wall_material] = len(materials)
        materials.append({
            "name": interior_wall_cap_material_name(wall_material),
            "pbrMetallicRoughness": {
                "baseColorFactor": [0.0, 0.0, 0.0, 1.0],
                "metallicFactor": 0.0,
                "roughnessFactor": 1.0,
            },
            "extensions": {"KHR_materials_unlit": {}},
        })
    material_names_by_index = {
        index: str(material.get("name") or f"material_{index}").strip()
        for index, material in enumerate(materials)
        if isinstance(material, dict)
    }
    kept_material_indices: set[int] = set()
    kept_primitive_count = 0
    component_cursor: dict[str, int] = defaultdict(int)

    for mesh_index, mesh in enumerate(gltf.get("meshes") or []):
        if not isinstance(mesh, dict):
            continue
        mesh_name = str(mesh.get("name") or f"mesh_{mesh_index}").strip()
        kept = []
        for primitive in mesh.get("primitives") or []:
            if not isinstance(primitive, dict):
                continue
            material_index = primitive.get("material")
            if isinstance(material_index, int):
                label = material_names_by_index.get(material_index, f"material_{material_index}")
                include = label.casefold() in selected
            else:
                label = mesh_name
                include = mesh_name.casefold() in selected
            triangles = _primitive_triangle_indices(glb, primitive)
            filtered_to_vertical_faces = include and label.casefold() in vertical_faces_only
            cap_triangles: list[tuple[int, int, int]] = []
            if filtered_to_vertical_faces:
                if label.casefold() in black_wall_caps:
                    cap_triangles = _upward_face_triangles(glb, primitive, triangles)
                triangles = _vertical_face_triangles(glb, primitive, triangles)
                if cutaway_edge and cutaway_coordinate is not None:
                    triangles = _without_cutaway_edge_faces(
                        glb, primitive, triangles, cutaway_edge, cutaway_coordinate)
                    cap_triangles = _without_cutaway_edge_faces(
                        glb, primitive, cap_triangles, cutaway_edge, cutaway_coordinate)
            components = _triangle_components(triangles)
            component_start = component_cursor[label.casefold()]
            component_cursor[label.casefold()] += max(1, len(components))
            if not include:
                continue
            if filtered_to_vertical_faces and not triangles:
                continue
            if spatial_tile_bounds is not None and components:
                spatial_kept: list[dict] = []
                for component in components:
                    bounds = _component_position_bounds(glb, primitive, component)
                    matches_spatial_tile = (
                        _component_center_in_spatial_tile(bounds, spatial_tile_bounds)
                        if spatial_component_center_filter
                        else _component_intersects_spatial_tile(bounds, spatial_tile_bounds)
                    )
                    if not matches_spatial_tile:
                        continue
                    candidate = dict(primitive)
                    replaced = False
                    crosses_tile = (
                        not preserve_spatial_components
                        and _component_crosses_spatial_tile(bounds, spatial_tile_bounds)
                    )
                    if crosses_tile:
                        replaced = _replace_with_spatially_clipped_geometry(
                            glb,
                            gltf,
                            bin_chunk,
                            candidate,
                            component,
                            spatial_tile_bounds,
                        )
                        if not replaced:
                            # The component bounding box can overlap a cell
                            # even when none of its irregular triangles do.
                            # Never fall back to the whole map-wide surface.
                            continue
                    if not replaced:
                        _replace_with_component_geometry(
                            glb,
                            gltf,
                            bin_chunk,
                            candidate,
                            component,
                        )
                    spatial_kept.append(candidate)
                if not spatial_kept:
                    continue
                kept.extend(spatial_kept)
                kept_primitive_count += len(spatial_kept)
                if isinstance(material_index, int):
                    kept_material_indices.add(material_index)
                continue
            kept_primitive = dict(primitive)
            target = component_targets.get(label.casefold())
            selected_component: list[tuple[int, int, int]] | None = None
            if target is not None:
                local_index = target - component_start
                if not (0 <= local_index < len(components)):
                    continue
                selected_component = components[local_index]
                replaced = False
                if label.casefold() in repeat_patches:
                    replaced = _replace_with_repeat_patch(
                        glb,
                        gltf,
                        bin_chunk,
                        kept_primitive,
                        selected_component,
                        patch_size=patch_size,
                    )
                if not replaced:
                    _replace_with_component_geometry(
                        glb,
                        gltf,
                        bin_chunk,
                        kept_primitive,
                        selected_component,
                    )
            elif filtered_to_vertical_faces:
                _replace_with_component_geometry(
                    glb,
                    gltf,
                    bin_chunk,
                    kept_primitive,
                    triangles,
                )
            kept.append(kept_primitive)
            kept_primitive_count += 1
            if isinstance(material_index, int):
                kept_material_indices.add(material_index)
            black_cap_material_index = black_cap_material_indices.get(label.casefold())
            if cap_triangles and black_cap_material_index is not None and target is None:
                cap_primitive = dict(primitive)
                if _replace_with_component_geometry(
                    glb, gltf, bin_chunk, cap_primitive, cap_triangles
                ):
                    cap_primitive["material"] = black_cap_material_index
                    kept.append(cap_primitive)
                    kept_primitive_count += 1
                    kept_material_indices.add(black_cap_material_index)
        mesh["primitives"] = kept

    if not kept_primitive_count:
        available = ", ".join(sorted(set(material_names_by_index.values()))[:16])
        raise ValueError(f"None of the selected materials exist in this GLB. Available: {available}")

    material_remap = {
        old_index: new_index
        for new_index, old_index in enumerate(sorted(kept_material_indices))
    }
    gltf["materials"] = [materials[index] for index in sorted(kept_material_indices)]
    for mesh in gltf.get("meshes") or []:
        if not isinstance(mesh, dict):
            continue
        for primitive in mesh.get("primitives") or []:
            old_index = primitive.get("material")
            if isinstance(old_index, int):
                primitive["material"] = material_remap[old_index]

    _prune_material_images(gltf)
    kept_material_names = {
        material_names_by_index[index].casefold()
        for index in kept_material_indices
        if index in material_names_by_index
    }
    _prune_material_motion(gltf, kept_material_names)

    buffers = gltf.setdefault("buffers", [{}])
    if not buffers:
        buffers.append({})
    buffers[0]["byteLength"] = len(bin_chunk)
    if component_targets or spatial_tile_bounds or vertical_faces_only or black_wall_caps:
        gltf.setdefault("extras", {}).setdefault("rae", {})["tileSelection"] = {
            "components": component_targets,
            "repeatPatchMaterials": sorted(repeat_patches),
            "patchSize": float(patch_size),
            "preserveSpatialComponents": bool(preserve_spatial_components),
            "spatialComponentCenterFilter": bool(spatial_component_center_filter),
            "verticalFacesOnlyMaterials": sorted(vertical_faces_only),
            "blackWallCapMaterials": sorted(black_wall_caps),
            **({"cutawayEdge": cutaway_edge, "cutawayCoordinate": cutaway_coordinate}
               if cutaway_edge and cutaway_coordinate is not None else {}),
            **({"spatialTileBounds": list(spatial_tile_bounds)} if spatial_tile_bounds else {}),
        }

    result = GlbData(json=gltf, bin_chunk=bytes(bin_chunk))
    if recenter:
        _recenter_selected_geometry(result, origin_y=origin_y)
    # The material table was compacted above. Rebuild portable UV-animation
    # targets so their JSON pointers reference the isolated material indices.
    from ..material_animation import sync_material_motion_property_animations

    sync_material_motion_property_animations(result)

    output.parent.mkdir(parents=True, exist_ok=True)
    result.write(output)
    return output
