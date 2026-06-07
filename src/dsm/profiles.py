from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True, slots=True)
class RomProfile:
    family: str
    generation: str | None = None
    confidence: str = "low"
    notes: tuple[str, ...] = field(default_factory=tuple)
    priority_queries: tuple[str, ...] = field(default_factory=tuple)
    priority_paths: tuple[str, ...] = field(default_factory=tuple)

    @property
    def label(self) -> str:
        if self.generation:
            return f"{self.family} {self.generation}"
        return self.family


GENERAL_DS_PROFILE = RomProfile(
    family="Nintendo DS / Nitro",
    confidence="generic",
    notes=(
        "Generic scan: ROM filesystem, nested NARC archives, LZ10-compressed blobs, and carved Nitro 3D files.",
        "Names are often numeric, especially in commercial games, so visual preview/export is usually required.",
    ),
    priority_queries=("BMD0", "BTX0", "NARC", "model", "map", "field", "boat", "ship", "ferry"),
)

POKEMON_GEN4_PROFILE = RomProfile(
    family="Pokémon",
    generation="Gen 4 DS",
    confidence="title/game-code heuristic",
    notes=(
        "Pokémon Gen 4 uses many NARC containers and many numbered assets.",
        "Useful field/map props are often bundled with larger map or field models rather than named standalone objects.",
        "Search by BMD0 first, then by folders containing field, map, building, object, or model names when present.",
    ),
    priority_queries=("BMD0", "BTX0", "field", "map", "model", "building", "object", "boat", "ship", "ferry"),
    priority_paths=("data/", "field", "map", "model", "build", "obj"),
)

POKEMON_GEN5_PROFILE = RomProfile(
    family="Pokémon",
    generation="Gen 5 DS",
    confidence="title/game-code heuristic",
    notes=(
        "Pokémon Gen 5 stores many assets under numbered /a/... NARC paths.",
        "For Black 2 / White 2-style hunting, /a/0/0/8 is a strong model-hunting starting point.",
        "Map textures and nearby BTX0 texture files may be separate from BMD0 model files.",
        "A prop can be embedded inside a larger city/dock map model, so export/convert the whole model and separate the mesh in Blender if needed.",
    ),
    priority_queries=("BMD0", "BTX0", "a/0/0/8", "a/0/1/4", "a/1/5/8", "boat", "ship", "ferry", "dock"),
    priority_paths=("a/0/0/8", "a/0/1/4", "a/1/5/8", "a/", "map", "model", "dock"),
)


def detect_profile(title: str, game_code: str, paths: Iterable[str] = ()) -> RomProfile:
    """Return a best-effort profile for friendly hints, without limiting scanning.

    The scanner is intentionally generic. Profiles only add sorting/search hints.
    """
    title_u = (title or "").upper()
    code_u = (game_code or "").upper()
    paths_l = tuple((p or "").lower() for p in paths)

    is_pokemon = "POKEMON" in title_u or "POKÉMON" in title_u
    if not is_pokemon:
        return GENERAL_DS_PROFILE

    # Gen 5 Pokémon ROMs usually lean heavily on /a/... numbered archives.
    # B/W and B2/W2 game codes in many regions start with IR, but we keep this
    # as a soft hint only because regional/prototype dumps vary.
    has_numbered_a = any(p.startswith("a/") or "/a/" in p for p in paths_l)
    if has_numbered_a or code_u.startswith("IR"):
        return POKEMON_GEN5_PROFILE

    # Gen 4 Pokémon games frequently expose named data folders, and several
    # regional game codes start with A, C, or I. Again: this is only for hints.
    if any(p.startswith("data/") for p in paths_l) or code_u[:1] in {"A", "C", "I"}:
        return POKEMON_GEN4_PROFILE

    return RomProfile(
        family="Pokémon",
        generation="DS",
        confidence="title heuristic",
        notes=(
            "Pokémon DS ROM detected. Running the same generic DS/Nitro scan with Pokémon-oriented search hints.",
        ),
        priority_queries=("BMD0", "BTX0", "NARC", "field", "map", "model", "boat", "ship", "ferry"),
    )
