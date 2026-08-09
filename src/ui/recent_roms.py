"""Persist recently opened ROM paths for the desktop shell."""
from __future__ import annotations

import json
from pathlib import Path

from ..install import project_root

MAX_RECENT_ROMS = 5


def _store_path() -> Path:
    path = project_root() / ".cache" / "recent_roms.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _read_store() -> list[str]:
    path = _store_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: list[str] = []
    for item in data:
        if isinstance(item, str) and item and item not in out:
            out.append(item)
        if len(out) >= MAX_RECENT_ROMS:
            break
    return out


def _write_store(paths: list[str]) -> None:
    trimmed = paths[:MAX_RECENT_ROMS]
    _store_path().write_text(json.dumps(trimmed, indent=2) + "\n", encoding="utf-8")


def load_recent_roms() -> list[str]:
    """Return stored ROM paths that still exist on disk, most recent first."""
    return [path for path in _read_store() if Path(path).exists()]


def remember_recent_rom(path: str) -> list[str]:
    """Move *path* to the front of the recent-ROM list and persist it."""
    resolved = str(Path(path).expanduser().resolve())
    recent = [entry for entry in _read_store() if entry != resolved]
    recent.insert(0, resolved)
    _write_store(recent)
    return load_recent_roms()
