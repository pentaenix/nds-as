"""Reviewed Pokémon Black 2 map catalog corrections.

These rules describe ROM-specific topology that cannot be inferred reliably
from matrix adjacency alone: unused filler cells, story-state variants, and
loading-zone-separated rooms that share one official location label.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models_resource_map_catalog import MapScene, MapSubmission


_FILLER_MAP_IDS = {104, 106}  # map_outtree / map_outsea


def _replace_submission(item: MapSubmission, **changes: object) -> MapSubmission:
    return replace(item, **changes)


def _scene_without_cells(scene: MapScene, excluded: set[int]) -> MapScene | None:
    cells = tuple(cell for cell in scene.cells if cell.map_id not in excluded)
    return replace(scene, cells=cells) if cells else None


def _take(items: list[MapSubmission], titles: set[str]) -> list[MapSubmission]:
    return [item for item in items if item.title in titles]


def _combine(
    members: list[MapSubmission],
    *,
    key: str,
    title: str,
    scene_names: tuple[str, ...] | None = None,
    one_dae: bool = False,
) -> MapSubmission:
    if not members:
        raise ValueError(f"Cannot build reviewed map group {title!r} without members")
    scenes = tuple(scene for item in members for scene in item.scenes)
    if scene_names is not None:
        scenes = tuple(replace(scene, name=name) for scene, name in zip(scenes, scene_names, strict=True))
    first = members[0]
    return replace(
        first,
        key=key,
        title=title,
        notes="Reviewed grouping of closely related authored map variants.",
        scenes=scenes,
        combine_scenes_in_dae=one_dae,
    )


def _split_rows(item: MapSubmission, *, names: tuple[str, ...]) -> MapSubmission:
    scene = item.scenes[0]
    rows = sorted({cell.y for cell in scene.cells})
    scenes = tuple(
        replace(scene, name=name, cells=tuple(cell for cell in scene.cells if cell.y == row))
        for row, name in zip(rows, names, strict=True)
    )
    return replace(item, scenes=scenes, notes="Authored vertical levels kept as separate DAE scenes.")


def apply_black2_catalog_corrections(submissions: list[MapSubmission]) -> list[MapSubmission]:
    """Apply the reviewed Black 2 submission topology and exact cell overrides."""
    cleaned: list[MapSubmission] = []
    for item in submissions:
        scenes = tuple(
            filtered
            for scene in item.scenes
            if (filtered := _scene_without_cells(scene, _FILLER_MAP_IDS)) is not None
        )
        if scenes:
            cleaned.append(replace(item, scenes=scenes))

    drop_titles = {
        "Cold Storage",  # unused BW map; PWT is the shipped B2 location
        "Nacrene Gym (Legacy) Area 3",
        "Opelucid City Area 2",
        "Opelucid City Building 1 Area 2",
        "Team Plasma House (Legacy) Legacy Scene",
        "Unity Tower",
    }
    items = [item for item in cleaned if item.title not in drop_titles]

    rewritten: list[MapSubmission] = []
    for item in items:
        if item.title == "Black City Pokémon Center":
            item = _replace_submission(
                item,
                key="interiors:shared-pokemon-center",
                title="Pokémon Center",
            )
        elif item.title == "Driftveil Drawbridge":
            item = replace(
                item,
                scenes=tuple(
                    replace(scene, cells=tuple(replace(cell, include_objects=False) for cell in scene.cells))
                    for scene in item.scenes
                ),
                notes="Bridge terrain only; its actor table is not an outside-building placement table.",
            )
        elif item.title == "Nimbasa City Area 3":
            cells = tuple(
                cell for cell in item.scenes[0].cells
                if cell.map_id in {*range(29, 38), 250}
            )
            item = replace(
                item,
                key="maps:route-4-legacy",
                title="Route 4 (Legacy)",
                location_name="Route 4",
                scenes=(replace(item.scenes[0], cells=cells),),
                notes="Legacy Route 4 cells separated from the adjacent Nimbasa matrix component.",
            )
        elif item.title == "Route 19":
            item = replace(
                item,
                scenes=tuple(
                    replace(
                        scene,
                        cells=tuple(replace(cell, area=210) if cell.map_id == 281 else cell for cell in scene.cells),
                    )
                    for scene in item.scenes
                ),
                notes="Uses the exact spring texture area for map01_21 and the adjacent area for map02_21.",
            )
        elif item.section == "Interior Maps" and item.title == "P2 Laboratory":
            item = replace(
                item,
                scenes=(replace(item.scenes[0], cells=tuple(cell for cell in item.scenes[0].cells if cell.map_id == 935)),),
                notes="Laboratory room only; the unrelated outdoor auxiliary cell is excluded.",
            )
        elif item.title == "Champion Iris's Room":
            item = replace(
                item,
                scenes=tuple(
                    replace(
                        scene,
                        cells=tuple(replace(cell, remove_black_vertex_colors=True) for cell in scene.cells),
                    )
                    for scene in item.scenes
                ),
                notes="Drops all-zero baked vertex lighting so the authored floor texture remains visible.",
            )
        elif item.title in {"Relic Passage Area 1", "Relic Passage Areas 2-3"}:
            item = replace(
                item,
                scenes=tuple(
                    replace(
                        scene,
                        cells=tuple(
                            replace(cell, additional_map_textures=(0,))
                            if cell.map_id in {713, 714, 658} else cell
                            for cell in scene.cells
                        ),
                    )
                    for scene in item.scenes
                ),
            )
        elif item.title == "Driftveil Gym":
            item = _split_rows(item, names=("Upper Level", "Middle Level", "Lower Level"))
        elif item.title == "Nimbasa Gym (Legacy)":
            scene = item.scenes[0]
            item = replace(item, scenes=(replace(scene, cells=scene.cells[:2]),))
        elif item.title == "Nuvema Town House 1 2F":
            item = replace(
                item,
                scenes=tuple(
                    replace(
                        scene,
                        cells=tuple(replace(cell, max_component_center_x=100.0) for cell in scene.cells),
                    )
                    for scene in item.scenes
                ),
                notes="Excludes the unreachable auxiliary room authored far outside the playable interior.",
            )
        rewritten.append(item)
    items = rewritten

    # White-version Opelucid is present in Black 2's ROM as a complete authored variant.
    opelucid = next(item for item in items if item.section == "Maps" and item.title == "Opelucid City")
    white_ids = {74: 200, 75: 201, 76: 202, 77: 203}
    white_scenes = tuple(
        replace(
            scene,
            cells=tuple(replace(cell, map_id=white_ids[cell.map_id], area=53) for cell in scene.cells),
        )
        for scene in opelucid.scenes
    )
    items.append(replace(
        opelucid,
        key="maps:opelucid-city-white-version",
        title="Opelucid City (White Version)",
        notes="Complete White-version city variant stored in the Black 2 ROM.",
        scenes=white_scenes,
    ))

    groups: list[tuple[set[str], MapSubmission]] = []
    entree_titles = {"Entree Forest", *(f"Entree Forest Area {number}" for number in range(3, 10))}
    entree = _take(items, entree_titles)
    groups.append((entree_titles, _combine(
        entree,
        key="maps:entree-forest-variants",
        title="Entree Forest",
        scene_names=tuple("Main Area" if item.title == "Entree Forest" else item.title.removeprefix("Entree Forest ") for item in entree),
        one_dae=True,
    )))
    plasma_titles = {"Plasma Frigate Area 7", "Plasma Frigate Area 8", *(f"Plasma Frigate Area {number}" for number in range(10, 18))}
    plasma = _take(items, plasma_titles)
    groups.append((plasma_titles, _combine(
        plasma,
        key="interiors:plasma-frigate-maze-variants",
        title="Plasma Frigate Maze",
        scene_names=tuple(item.title.removeprefix("Plasma Frigate ") for item in plasma),
        one_dae=True,
    )))

    castelia_titles = {"Castelia City Building 1", "Castelia City Building 4"}
    castelia = sorted(_take(items, castelia_titles), key=lambda item: item.title)
    groups.append((castelia_titles, _combine(
        castelia, key="interiors:castelia-building-1-complete",
        title="Castelia City Building 1", scene_names=("Main Interior", "Related Interior"),
    )))
    strange_one = {"Strange House Area 1", "Strange House Area 6"}
    groups.append((strange_one, _combine(
        _take(items, strange_one), key="interiors:strange-house-area-1-6",
        title="Strange House Areas 1 and 6", scene_names=("Area 1", "Area 6"),
    )))
    strange_late = {*(f"Strange House Area {number}" for number in range(7, 11))}
    strange_late_items = sorted(_take(items, strange_late), key=lambda item: int(item.title.rsplit(" ", 1)[-1]))
    groups.append((strange_late, _combine(
        strange_late_items, key="interiors:strange-house-areas-7-10",
        title="Strange House Areas 7-10",
        scene_names=tuple(f"Area {number}" for number in range(7, 11)),
    )))

    skyarrow_titles = {"Skyarrow Bridge", "Bridge Gate"}
    skyarrow = sorted(_take(items, skyarrow_titles), key=lambda item: item.title != "Skyarrow Bridge")
    groups.append((skyarrow_titles, _combine(
        skyarrow, key="maps:skyarrow-bridge-complete",
        title="Skyarrow Bridge", scene_names=("Bridge", "Entrance and Staircase"),
    )))
    grouped_titles = set().union(*(titles for titles, _group in groups))
    items = [item for item in items if item.title not in grouped_titles]
    items.extend(group for _titles, group in groups)

    clay = next((item for item in items if item.title == "Clay Tunnel Areas 2-3"), None)
    if clay is not None:
        items.remove(clay)
        matrices = sorted({cell.matrix for scene in clay.scenes for cell in scene.cells})
        for number, matrix in enumerate(matrices, 2):
            cells = tuple(cell for scene in clay.scenes for cell in scene.cells if cell.matrix == matrix)
            items.append(replace(
                clay,
                key=f"interiors:clay-tunnel-area-{number}",
                title=f"Clay Tunnel Area {number}",
                scenes=(replace(clay.scenes[0], name=f"Area {number}", cells=cells),),
                notes="Loading-zone matrix retained as an independent cave area.",
            ))
    return items
