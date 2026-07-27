"""Semantic Ultra Sun/Ultra Moon environments used by Pokemon Attend.

This catalog belongs to the 3DS platform island.  It is intentionally usable
from both the UI export module and headless callers so complete environment
packages are reproducible without clicking through individual GARC slots.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .world_composition import BATTLE_BACKGROUND_GARC, composition_by_id


@dataclass(frozen=True, slots=True)
class AttendEnvironmentSpec:
    id: str
    label: str
    slots: tuple[int, ...]
    composition_id: str | None = None
    route: str = "manual"


def _composed(
    scene_id: str,
    label: str,
    centre: int,
    outer: int,
    route: str,
) -> AttendEnvironmentSpec:
    return AttendEnvironmentSpec(
        id=scene_id,
        label=label,
        slots=(outer, centre),
        composition_id=f"battle_{centre:04d}_{outer:04d}",
        route=route,
    )


ATTEND_ENVIRONMENTS: tuple[AttendEnvironmentSpec, ...] = (
    _composed("alola_open_sea", "Alola Open Sea", 9, 103, "surface:sea"),
    _composed("alola_coastal_ocean", "Alola Coastal Ocean", 9, 118, "surface:sea"),
    _composed("alola_beach", "Alola Beach", 8, 102, "surface:beach,sand"),
    _composed("alola_flower_field", "Alola Flower Field", 4, 99, "surface:flower"),
    AttendEnvironmentSpec("alola_grass_closed", "Alola Grass Field", (3,), route="surface:grass"),
    _composed("alola_grass_arena", "Alola Grass Arena", 0, 96, "surface:grass,ground"),
    _composed("alola_grass_hill", "Alola Grass Hill", 70, 131, "surface:grass"),
    AttendEnvironmentSpec("alola_dirt_field", "Alola Dirt Field", (17,), route="surface:dirt"),
    AttendEnvironmentSpec("alola_freshwater", "Alola Freshwater", (19,), route="surface:freshwater"),
    _composed("alola_mountain", "Alola Mountain", 23, 106, "surface:rock"),
    _composed("alola_asian_garden", "Alola Asian Garden", 27, 109, "manual"),
    _composed("alola_waterfall", "Alola Waterfall", 30, 111, "surface:river"),
    AttendEnvironmentSpec("alola_dry_grass", "Alola Dry Grass", (60,), route="surface:dry_grass"),
    AttendEnvironmentSpec("alola_bathroom", "Alola Bathroom", (11,), route="manual"),
    AttendEnvironmentSpec("alola_dream_visit", "Alola Dream Visit", (64,), route="event:dream_visit"),
    AttendEnvironmentSpec("alola_hooh_celebi", "Ho-Oh and Celebi", (75,), route="pokemon:ho_oh,celebi"),
    AttendEnvironmentSpec("alola_underwater", "Alola Underwater", (76,), route="pokemon_group:aquatic"),
    AttendEnvironmentSpec("alola_arceus", "Arceus", (77,), route="pokemon:arceus"),
    AttendEnvironmentSpec("alola_rayquaza_deoxys", "Rayquaza and Deoxys", (81,), route="pokemon:rayquaza,deoxys"),
    _composed("alola_groudon_cliff", "Groudon Cliff", 1, 97, "pokemon:groudon"),
    AttendEnvironmentSpec("alola_guzzlord", "Guzzlord", (82,), route="pokemon:guzzlord"),
    AttendEnvironmentSpec("alola_meloetta", "Meloetta", (83,), route="pokemon:meloetta"),
    AttendEnvironmentSpec("alola_party", "Alola Party", (84,), route="event:party"),
    AttendEnvironmentSpec("alola_special_0086", "Alola Special 0086", (86,), route="manual"),
    AttendEnvironmentSpec("alola_special_0092", "Alola Special 0092", (92,), route="manual"),
)

_BY_ID = {item.id: item for item in ATTEND_ENVIRONMENTS}


def attend_environment_by_id(scene_id: str) -> AttendEnvironmentSpec | None:
    return _BY_ID.get(str(scene_id))


def attend_environment_ids() -> tuple[str, ...]:
    return tuple(item.id for item in ATTEND_ENVIRONMENTS)


def descriptor_for_attend_environment(base: dict, scene_id: str) -> dict:
    """Return a world descriptor targeting one semantic Attend environment."""
    spec = attend_environment_by_id(scene_id)
    if spec is None:
        raise KeyError(f"unknown Pokemon Attend environment: {scene_id}")
    if str(base.get("garc") or "") != BATTLE_BACKGROUND_GARC:
        raise ValueError(
            f"Pokemon Attend environments require {BATTLE_BACKGROUND_GARC}, "
            f"got {base.get('garc')!r}"
        )
    out = {
        **base,
        "type": "world_model",
        "slot": spec.slots[-1],
        "name": spec.label,
        "environment_scene_id": spec.id,
        "environment_scene_label": spec.label,
        "environment_route": spec.route,
    }
    if spec.composition_id:
        composition = composition_by_id(spec.composition_id)
        if composition is None:
            raise ValueError(f"missing world composition {spec.composition_id}")
        out.update(
            composition_id=composition.id,
            composition_label=composition.label,
            composition_slots=list(composition.slots),
            composition_outer_slot=composition.outer_slot,
        )
    else:
        for key in (
            "composition_id",
            "composition_label",
            "composition_slots",
            "composition_outer_slot",
        ):
            out.pop(key, None)
    return out


def selected_attend_environments(ids: Iterable[str] | None) -> list[AttendEnvironmentSpec]:
    if ids is None:
        return list(ATTEND_ENVIRONMENTS)
    selected: list[AttendEnvironmentSpec] = []
    for scene_id in ids:
        spec = attend_environment_by_id(scene_id)
        if spec is None:
            raise KeyError(f"unknown Pokemon Attend environment: {scene_id}")
        selected.append(spec)
    return selected
