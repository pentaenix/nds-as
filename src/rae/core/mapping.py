from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class ArchiveMapping:
    path: str
    label: str
    category: str
    confidence: str
    asset_types: tuple[str, ...]
    tags: tuple[str, ...]


@dataclass(slots=True)
class SearchPreset:
    id: str
    label: str
    query: str
    description: str
    workflow: tuple[str, ...]


MAPPING_PLATFORM_DIRS = ("nds", "gba", "gbc", "gb", "3ds")


@dataclass(slots=True)
class GameMapping:
    mapping_id: str
    platform: str
    game_family: str
    coverage: str
    notes: str | list[str]
    games: list[dict]
    archives: list[ArchiveMapping]
    ui_groups: list[dict]
    sources: list[dict]
    search_presets: list[SearchPreset]
    path: Path | None = None

    @property
    def label(self) -> str:
        titles = []
        for game in self.games:
            title = game.get("shortTitle") or game.get("title")
            if title:
                titles.append(title)
        return ", ".join(titles[:3]) or self.mapping_id


def mappings_dir() -> Path:
    candidates: list[Path] = []
    here = Path(__file__).resolve()
    candidates.append(Path.cwd() / "mappings")
    for parent in here.parents:
        candidates.append(parent / "mappings")
    for candidate in candidates:
        if candidate.exists() and candidate.is_dir():
            return candidate
    return Path.cwd() / "mappings"


def _mapping_json_paths(root: Path) -> list[Path]:
    paths: list[Path] = []
    for platform_dir in MAPPING_PLATFORM_DIRS:
        sub = root / platform_dir
        if sub.is_dir():
            paths.extend(sorted(sub.glob("*.json")))
    # Legacy flat layout (pre-RAE); ignore once everything lives under platform folders.
    for path in sorted(root.glob("*.json")):
        if path.name != "schema.json" and path not in paths:
            paths.append(path)
    return paths


def _parse_mapping_file(path: Path, *, default_platform: str) -> GameMapping | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    platform = str(raw.get("platform", default_platform))
    archives = []
    for a in raw.get("archives", []):
        archives.append(ArchiveMapping(
            path=str(a.get("path", "")),
            label=str(a.get("label", "")),
            category=str(a.get("category", "unknown")),
            confidence=str(a.get("confidence", "")),
            asset_types=tuple(str(x) for x in a.get("assetTypes", [])),
            tags=tuple(str(x) for x in a.get("tags", [])),
        ))
    presets = []
    for preset in raw.get("searchPresets", []):
        presets.append(SearchPreset(
            id=str(preset.get("id", "")),
            label=str(preset.get("label", "")),
            query=str(preset.get("query", "")),
            description=str(preset.get("description", "")),
            workflow=tuple(str(x) for x in preset.get("workflow", [])),
        ))
    return GameMapping(
        mapping_id=str(raw.get("mappingId", path.stem)),
        platform=platform,
        game_family=str(raw.get("gameFamily", "")),
        coverage=str(raw.get("coverage", "")),
        notes=raw.get("notes", ""),
        games=list(raw.get("games", [])),
        archives=archives,
        ui_groups=list(raw.get("uiGroups", [])),
        sources=list(raw.get("sources", [])),
        search_presets=presets,
        path=path,
    )


def load_mappings(platform: str | None = None) -> list[GameMapping]:
    root = mappings_dir()
    maps: list[GameMapping] = []
    for path in _mapping_json_paths(root):
        default_platform = path.parent.name if path.parent.name in MAPPING_PLATFORM_DIRS else "nds"
        mapping = _parse_mapping_file(path, default_platform=default_platform)
        if mapping is None:
            continue
        if platform is not None and mapping.platform != platform:
            continue
        maps.append(mapping)
    return maps


def choose_mapping(title: str, game_code: str, available: Iterable[GameMapping] | None = None) -> GameMapping | None:
    maps = list(available if available is not None else load_mappings())
    code = (game_code or "").upper().strip()
    title_l = (title or "").casefold().strip()
    # Several Pokémon DS headers use compact internal titles such as
    # "POKEMON B2" / "POKEMON W2" rather than the retail title. Prefer these
    # before falling back to the generic wildcard mapping.
    compact = title_l.replace("é", "e")
    forced_id = ""
    if "pokemon" in compact:
        if " b2" in compact or "black 2" in compact or " w2" in compact or "white 2" in compact:
            forced_id = "pokemon_bw2"
        elif compact.endswith(" b") or compact.endswith(" w") or "black" in compact or "white" in compact:
            forced_id = "pokemon_bw"
    if forced_id:
        for mapping in maps:
            if mapping.mapping_id == forced_id:
                return mapping

    best: tuple[int, GameMapping] | None = None
    for mapping in maps:
        score = 0
        if mapping.mapping_id == "generic_nds":
            score = 1
        for game in mapping.games:
            for pattern in game.get("romCodes", []):
                pat = str(pattern).upper()
                if pat == "*" and score < 1:
                    score = 1
                elif code and fnmatch.fnmatch(code, pat):
                    score = max(score, 100)
            game_title = str(game.get("title", "")).casefold()
            short = str(game.get("shortTitle", "")).casefold()
            if game_title and game_title in title_l:
                score = max(score, 80)
            if short and short in title_l:
                score = max(score, 60)
        if score and (best is None or score > best[0]):
            best = (score, mapping)
    return best[1] if best else None


def apply_mapping_to_assets(assets: list, mapping: GameMapping | None) -> None:
    if mapping is None:
        return
    for asset in assets:
        match = match_asset(asset, mapping)
        if match:
            asset.mapping_category = match.category
            asset.mapping_label = match.label
            asset.mapping_confidence = match.confidence
        elif asset.mapping_category == "unknown":
            # Generic category from magic. Mapping files are better, but this makes
            # Raw/Unmapped browsing useful immediately.
            asset.mapping_category = category_from_magic(asset.magic)
            asset.mapping_label = "Detected by signature"
            asset.mapping_confidence = "format-signature"


def match_asset(asset, mapping: GameMapping) -> ArchiveMapping | None:
    path = normalize_path(asset.virtual_path)
    best: tuple[int, ArchiveMapping] | None = None
    for archive in mapping.archives:
        score = archive_match_score(path, asset.magic, archive)
        if score and (best is None or score > best[0]):
            best = (score, archive)
    return best[1] if best else None


def normalize_path(path: str) -> str:
    return "/" + path.replace("\\", "/").strip("/").casefold()


def archive_match_score(asset_path: str, magic: str, archive: ArchiveMapping) -> int:
    raw = archive.path.strip()
    if not raw:
        return 0
    pat = normalize_path(raw)
    # Glob entries from generic mappings.
    if "*" in pat or "{" in pat:
        patterns = expand_brace_pattern(pat)
        for p in patterns:
            if fnmatch.fnmatch(asset_path, p) or fnmatch.fnmatch("/" + Path(asset_path).name, p):
                return 40
        # Asset type can still category-match if magic is known.
        if archive.asset_types and magic and magic.upper() in {x.upper() for x in archive.asset_types}:
            return 20
        return 0
    if asset_path == pat:
        return 100
    if asset_path.startswith(pat.rstrip("/") + "/"):
        return 90
    # Some NARC children show as a/... without a leading slash inside container chains.
    if pat.strip("/") in asset_path.strip("/").split("/"):
        return 30
    if archive.asset_types and magic and magic.upper() in {x.upper() for x in archive.asset_types}:
        return 10
    return 0


def expand_brace_pattern(pattern: str) -> list[str]:
    if "{" not in pattern:
        return [pattern]
    start = pattern.find("{")
    end = pattern.find("}", start)
    if end < 0:
        return [pattern]
    before = pattern[:start]
    after = pattern[end + 1:]
    items = pattern[start + 1:end].split(",")
    return [before + item + after for item in items]


def category_from_magic(magic: str) -> str:
    if magic in {"BMD0"}:
        return "models"
    if magic in {"BTX0"}:
        return "textures"
    if magic in {"BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0", "RNAN", "RECN"}:
        return "animations"
    if magic in {"RGCN", "RLCN", "RCSN", "PNG"}:
        return "images-tiles"
    if magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
        return "audio"
    if magic == "NFTR":
        return "fonts"
    return "unknown"


def mapping_summary(mapping: GameMapping | None) -> str:
    if mapping is None:
        return "Mapping: none"
    notes = mapping.notes
    if isinstance(notes, list):
        notes_s = " ".join(str(n) for n in notes[:2])
    else:
        notes_s = str(notes)
    return f"Mapping: {mapping.mapping_id} [{mapping.platform}] ({mapping.coverage}). {notes_s}".strip()
