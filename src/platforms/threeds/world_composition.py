"""Ultra Sun/Ultra Moon battle-background layer compositions.

The `/a/0/8/1` archive stores arena centres (mostly ``btl_G_*``) separately
from their much wider surroundings (mostly ``btl_N_*``).  The game combines
them at the same origin.  Keep this catalog inside the 3DS island: these slot
relationships and naming conventions are Game Freak/USUM-specific.
"""
from __future__ import annotations

from dataclasses import dataclass


BATTLE_BACKGROUND_GARC = "/a/0/8/1"


@dataclass(frozen=True, slots=True)
class WorldComposition:
    id: str
    label: str
    slots: tuple[int, ...]
    outer_slot: int
    confidence: str = "family-name"


def _pair(inner: int, outer: int, family: str) -> WorldComposition:
    return WorldComposition(
        id=f"battle_{inner:04d}_{outer:04d}",
        label=f"Complete {family} arena (centre {inner:04d} + outer {outer:04d})",
        slots=(outer, inner),
        outer_slot=outer,
    )


# Verified structurally from the G/N material-family names and centered model
# bounds. Entries omitted here are self-contained interiors/special stages.
WORLD_COMPOSITIONS: tuple[WorldComposition, ...] = (
    _pair(0, 96, "grass"),
    _pair(1, 97, "windy cliff"),
    _pair(2, 98, "cemetery"),
    _pair(4, 99, "flower field"),
    _pair(5, 100, "Lillie garden"),
    _pair(6, 101, "city"),
    _pair(7, 104, "grass variant"),
    _pair(8, 102, "beach"),
    _pair(9, 103, "open sea"),
    # The pure sea centre is also used inside the wider ham2 coast shell.
    WorldComposition(
        id="battle_0009_0118",
        label="Complete coastal ocean arena (centre 0009 + outer 0118)",
        slots=(118, 9),
        outer_slot=118,
        confidence="observed-composition",
    ),
    _pair(21, 105, "school grounds"),
    _pair(23, 106, "mountain"),
    _pair(24, 107, "beach 1"),
    _pair(26, 108, "snow"),
    _pair(27, 109, "Asian garden"),
    _pair(28, 110, "desert"),
    _pair(30, 111, "waterfall"),
    _pair(31, 112, "canyon"),
    _pair(33, 114, "red field"),
    _pair(34, 115, "purple field"),
    _pair(35, 116, "Po Town"),
    _pair(36, 117, "Paniola"),
    _pair(37, 118, "beach 2"),
    _pair(45, 119, "exterior trial"),
    _pair(47, 120, "trial path"),
    _pair(48, 121, "Pokémon League monument"),
    _pair(49, 122, "Pokémon League monument variant"),
    _pair(51, 123, "battle tree"),
    _pair(53, 124, "mountain variant"),
    _pair(56, 125, "marina"),
    _pair(66, 127, "battle tree tier 1"),
    _pair(67, 128, "battle tree tier 2"),
    _pair(68, 129, "rocky field"),
    _pair(69, 130, "temple"),
    _pair(70, 131, "grass hill"),
)


def compositions_for_slot(slot: int) -> tuple[WorldComposition, ...]:
    return tuple(item for item in WORLD_COMPOSITIONS if slot in item.slots)


def composition_by_id(composition_id: str) -> WorldComposition | None:
    return next((item for item in WORLD_COMPOSITIONS if item.id == composition_id), None)


def default_composition_for_slot(slot: int) -> WorldComposition | None:
    choices = compositions_for_slot(slot)
    if not choices:
        return None
    # The user-observed 0009+0118 coast is the useful default for either of its
    # directly selected pieces; alternatives remain explicit export choices.
    preferred = next((item for item in choices if item.id == "battle_0009_0118"), None)
    return preferred or choices[0]


def apply_composition(descriptor: dict, composition: WorldComposition) -> dict:
    return {
        **descriptor,
        "composition_id": composition.id,
        "composition_label": composition.label,
        "composition_slots": list(composition.slots),
        "composition_outer_slot": composition.outer_slot,
    }

