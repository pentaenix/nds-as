"""Persistent review state and export jobs for unnamed LBX item props."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
import re

from ...install import project_root
from .container import RomFsFile
from .lbx_catalog import LbxExportJob


ITEM_ROOT = "/3ddata/item/"
STATE_VERSION = 1


def _natural_key(value: str) -> tuple[object, ...]:
    return tuple(
        int(part) if part.isdigit() else part.casefold()
        for part in re.split(r"(\d+)", value)
    )


def item_model_paths(entries: list[RomFsFile]) -> list[str]:
    return sorted(
        (
            entry.path
            for entry in entries
            if entry.path.startswith(ITEM_ROOT)
            and entry.path.casefold().endswith(".bcmdl")
        ),
        key=_natural_key,
    )


def proposed_item_title(path: str) -> str:
    stem = PurePosixPath(path).stem
    match = re.fullmatch(r"etc(\d+)_([a-z0-9_]+)", stem, re.IGNORECASE)
    if not match:
        return sanitize_submission_title(stem.replace("_", " ").title())
    variant = " ".join(part.upper() for part in match.group(2).split("_") if part)
    return f"Item {match.group(1)} Variant {variant}"


def sanitize_submission_title(value: str) -> str:
    clean = re.sub(r"[\\/:*?\"<>|]+", " ", str(value))
    return " ".join(clean.split()).strip(". ")


_ITEM_RENAMES = {
    "Arena 15": "Arena 8",
    "Bulldozer 1": "Bulldozer Full",
}

_BULLDOZER_PARTS = {
    "Bulldozer Body": ("Parts", "Body"),
    "Bulldozer Head": ("Parts", "Head"),
    "Bulldozer Left Arm": ("Parts", "Left Arm"),
    "Bulldozer Right Arm": ("Parts", "Right Arm"),
}


def normalized_item_title(title: str) -> str:
    clean = sanitize_submission_title(title)
    return _ITEM_RENAMES.get(clean, clean)


def item_category(title: str) -> tuple[str, ...]:
    """Map reviewed LBX items to stable Models Resource package groups."""
    clean = normalized_item_title(title)
    folded = clean.casefold()
    if clean in _BULLDOZER_PARTS:
        return _BULLDOZER_PARTS[clean]
    if folded.startswith("phone "):
        return ("Items", "Phones")
    if folded.startswith("npc "):
        return ("Items", "NPCs")
    if folded.startswith("arena "):
        return ("Items", "Arenas")
    if " full" in f" {folded}":
        return ("Items", "Full Robots")
    if (
        folded.startswith(("car ", "traffic", "rocket", "truck "))
        or " plane" in f" {folded}"
    ):
        return ("Items", "Vehicles")
    return ("Items", "Props")


def review_state_path() -> Path:
    return project_root() / "exports" / "LBX" / "lbx_item_review.json"


def load_review_state(rom_path: str | Path) -> dict:
    rom = str(Path(rom_path).expanduser().resolve())
    path = review_state_path()
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    if state.get("version") != STATE_VERSION or state.get("rom") != rom:
        return {"version": STATE_VERSION, "rom": rom, "decisions": {}}
    if not isinstance(state.get("decisions"), dict):
        state["decisions"] = {}
    return state


def save_review_state(state: dict) -> Path:
    path = review_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    temporary.replace(path)
    return path


def item_export_job(path: str, title: str) -> LbxExportJob:
    clean = normalized_item_title(title)
    return LbxExportJob(
        romfs_path=path,
        category=item_category(clean),
        title=clean,
    )
