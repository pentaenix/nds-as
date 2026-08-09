"""Conservative names for Black 2 interiors derived from terrain identifiers."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class InteriorName:
    package: str
    scene: str
    confidence: str
    note: str


COMPOUND_LOCATIONS = {
    "Abyssal Ruins", "Battle Subway", "Black Tower", "Castelia Sewers",
    "Cave of Being", "Guidance Chamber", "Iceberg Chamber", "Iron Chamber",
    "Celestial Tower", "Chargestone Cave", "Clay Tunnel", "Dragonspiral Tower",
    "Gear Station", "Giant Chasm", "Mistralton Cave", "Musical Theater",
    "N's Castle", "Plasma Frigate", "Pokéstar Studios", "PWT", "Relic Castle",
    "Relic Passage", "Reversal Mountain", "Seaside Cave", "Shopping Mall",
    "Strange House", "Twist Mountain", "Underground Ruins", "Unity Tower",
    "Rock Peak Chamber", "Victory Road", "Wellspring Cave", "White Treehollow",
}

COMPOUND_TITLES = {
    "PWT": "Pokémon World Tournament",
    "Shopping Mall": "Shopping Mall Nine",
}

_EXACT: tuple[tuple[str, str, str], ...] = (
    ("m_atelier01", "Studio Castelia", "Main Room"),
    ("m_gym0301", "Castelia Gym", "Main Room"),
    ("m_gym0302", "Castelia Gym", "Top Floor"),
    ("m_bar01", "Café Sonata", "Main Room"),
    ("m_live01", "Game Freak HQ", "Office"),
    ("m_medal01", "Medal Office", "Main Office"),
    ("m_market01", "Driftveil Market", "Main Room"),
    ("m_adc01", "Alder's House", "Main Room"),
    ("m_airport01", "Mistralton Cargo Service", "Terminal"),
    ("m_daisuki01", "Pokémon Fan Club", "Main Room"),
    ("m_grow01", "Pokémon Day Care", "Main Room"),
    ("m_nschool01", "Pokémon Nursery", "Classroom"),
    ("m_nschool02", "Virbank Trainers' School", "Classroom"),
    ("m_wlabo01", "Season Research Lab", "Laboratory"),
    ("m_pal01", "Poké Transfer Lab", "Laboratory"),
    ("m_labo01", "Professor Juniper's Lab", "Laboratory"),
    ("m_labo02", "Fennel's Lab", "Laboratory"),
    ("m_labo03", "P2 Laboratory", "Laboratory"),
    ("m_museum01", "Nacrene Museum", "Main Hall"),
    ("m_furniture01", "Nacrene Furniture Warehouse", "Interior"),
    ("m_cafe01", "Café Warehouse", "Main Room"),
    ("m_school01", "Striaton Trainers' School", "Classroom"),
    ("m_dining01", "Striaton Restaurant", "Dining Room"),
    ("m_tryhouse01", "Battle Institute", "Main Room"),
    ("m_stadium01", "Big Stadium", "Entrance"),
    ("m_stadium02", "Small Court", "Entrance"),
    ("m_tennis01", "Small Court", "Tennis Court"),
    ("m_basket01", "Small Court", "Basketball Court"),
    ("m_football01", "Big Stadium", "American Football Field"),
    ("m_soccer01", "Big Stadium", "Soccer Field"),
    ("m_baseball01", "Big Stadium", "Baseball Field"),
    ("m_pc01", "Black City Pokémon Center", "Main Room"),
    ("m_pc02", "Pokémon League Pokémon Center", "Main Room"),
    ("m_champ01", "Champion Iris's Room", "Battle Room"),
    ("m_dendo01", "Hall of Fame", "Hall"),
    ("m_siten01", "Elite Four Room 1", "Battle Room"),
    ("m_siten02", "Elite Four Room 2", "Battle Room"),
    ("m_siten03", "Elite Four Room 3", "Battle Room"),
    ("m_siten04", "Elite Four Room 4", "Battle Room"),
    ("m_gate01", "Route Gate", "Gatehouse"),
    ("m_gate02", "Nacrene Gate", "Gatehouse"),
    ("m_gate08", "Castelia Gate", "Gatehouse"),
    ("m_cabin01", "Royal Unova", "Passenger Cabin"),
    ("m_mall01", "Shopping Mall Nine", "Lower Floor"),
    ("m_mall02", "Shopping Mall Nine", "Upper Floor"),
    ("m_bcmall01", "Black City Market", "Main Hall"),
    ("m_ruin01", "Dreamyard Ruins", "Interior"),
    ("m_church01", "Team Plasma House", "Main Room"),
    ("m_oldchurch01", "Team Plasma House (Legacy)", "Legacy Scene"),
    ("m_d12h01", "Liberty Garden Lighthouse", "Interior"),
    ("m_d12h02", "Liberty Garden Basement", "Interior"),
    ("m_seat01", "Big Stadium", "Seating"),
    ("m_seat02", "Small Court", "Seating"),
    ("m_fha01", "Nuvema Town House 1", "1F"),
    ("m_fha02", "Nuvema Town House 1", "2F"),
    ("m_fhb01", "Nuvema Town House 2", "1F"),
    ("m_fhb02", "Nuvema Town House 2", "2F"),
    ("m_hh01", "Nuvema Town House 3", "1F"),
    ("m_hh02", "Nuvema Town House 3", "2F"),
    ("m_swanhh01", "Player's House", "Interior"),
    ("m_swanrh01", "Hugh's House", "Interior"),
    ("m_h04hole", "Village Bridge House", "Basement"),
    ("m_resonance01", "Join Avenue Office", "Office 1"),
    ("m_resonance02", "Join Avenue Office", "Office 2"),
    ("m_union01", "Union Room", "Main Room"),
    ("m_closseum01", "Colosseum", "Room 1"),
    ("m_closseum02", "Colosseum", "Room 2"),
)


def _gym_title(location: str, model: str) -> InteriorName:
    city = re.sub(r"\s+(City|Town)$", "", location)
    legacy = "bwgym" in model or "old" in model
    package = f"{city} Gym" + (" (Legacy)" if legacy else "")
    return InteriorName(package, "Main Room", "high", "Gym identifier in terrain model name.")


def suggest_interior_name(
    location: str,
    terrain_names: tuple[str, ...],
    ordinal: int,
) -> InteriorName:
    joined = ",".join(terrain_names).casefold()
    first = terrain_names[0].casefold() if terrain_names else ""
    for token, package, scene in _EXACT:
        if token.casefold() in joined:
            return InteriorName(package, scene, "high", f"Matched internal terrain identifier {token}.")
    if "gym" in joined:
        return _gym_title(location, joined)
    hotel = re.search(r"m_hotel(\d+)", joined)
    if hotel:
        return InteriorName(
            f"{location} Hotel", f"Room {int(hotel.group(1))}", "medium",
            "Hotel identifier is exact; room function is not encoded.",
        )
    warehouse = re.search(r"m_whouse(\d+)", joined)
    if warehouse:
        return InteriorName(
            f"{location} Warehouse {int(warehouse.group(1))}", "Interior", "medium",
            "Warehouse identifier in terrain model name.",
        )
    seaside_house = re.search(r"m_seahouse(\d+)", joined)
    if seaside_house:
        return InteriorName(
            f"{location} Seaside House", "Interior", "medium",
            "Seaside-house identifier in terrain model name.",
        )
    house = re.search(r"(?:^|_)h(\d+)", first)
    named_house = re.search(r"(?:[a-z]*house)(\d+)", first)
    if house or named_house:
        number = int((house or named_house).group(1))
        return InteriorName(
            f"{location} House {number}", "Interior", "medium",
            "House identifier in terrain model name; resident is not encoded.",
        )
    building = re.search(r"(?:[a-z]*buill)(\d+)", first)
    if building:
        return InteriorName(
            f"{location} Building {int(building.group(1))}", "Interior", "review",
            "Building/house identifier is present, but its public in-game name is not encoded.",
        )
    if "labo" in joined:
        return InteriorName(
            f"{location} Laboratory", "Laboratory", "medium",
            "Laboratory identifier in terrain model name; proper facility name needs review.",
        )
    if "school" in joined:
        return InteriorName(
            f"{location} School", "Classroom", "medium",
            "School identifier in terrain model name.",
        )
    if "gate" in joined:
        package = f"{location}house" if location.endswith("Gate") else f"{location} Gatehouse"
        if ordinal > 1:
            package += f" {ordinal}"
        return InteriorName(
            package, "Gatehouse", "medium",
            "Gate identifier is exact; connected destinations are not yet named.",
        )
    if first.startswith("m_old") or "old" in first:
        return InteriorName(
            f"{location} Legacy Scene {ordinal}", "Legacy Scene", "review",
            "Internal old/legacy terrain identifier; likely a retained BW or Memory Link scene.",
        )
    return InteriorName(
        f"{location} Interior {ordinal}" if ordinal > 1 else f"{location} Interior",
        "Interior", "review", "No reliable semantic token beyond the official location name.",
    )


def compound_scene_label(
    location: str,
    terrain_names: tuple[str, ...],
    ordinal: int,
) -> str:
    if location == "Abyssal Ruins":
        abyssal = ("Entrance 1", "Entrance 2", "Entrance 3", "Entrance 4", "1F", "2F", "3F", "4F")
        if 1 <= ordinal <= len(abyssal):
            return abyssal[ordinal - 1]
    if any(name.casefold().startswith("m_old") for name in terrain_names):
        return "Legacy Scene"
    return f"Area {ordinal}"


def is_white2_variant(terrain_names: tuple[str, ...]) -> bool:
    return bool(terrain_names) and all(name.casefold().startswith("white") for name in terrain_names)
