"""Canonical EasyFind storage paths keyed by Nintendo DS game code."""
from __future__ import annotations

import re
from pathlib import Path

from ..install import project_root
from .format import EASYFIND_EXTENSION

EASYFIND_DIR_NAME = "easyfind"
_GAME_CODE_RE = re.compile(r"^[A-Z0-9]{4}$")


def easyfind_store_dir(root: Path | None = None) -> Path:
    """Return the repo-local EasyFind folder (created on demand)."""
    base = root or project_root()
    path = base / EASYFIND_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def normalize_game_code(game_code: str) -> str:
    """Normalize a DS product code to uppercase alphanumeric (max 4 chars)."""
    cleaned = "".join(ch for ch in game_code.upper().strip() if ch.isalnum())
    return cleaned[:4]


def is_valid_game_code(game_code: str) -> bool:
    code = normalize_game_code(game_code)
    return bool(code) and bool(_GAME_CODE_RE.match(code))


def easyfind_path_for_game_code(game_code: str, *, root: Path | None = None) -> Path:
    """Return the canonical .easyfind path for a DS game code."""
    code = normalize_game_code(game_code)
    if not is_valid_game_code(code):
        raise ValueError(f"Invalid Nintendo DS game code: {game_code!r}")
    return easyfind_store_dir(root) / f"{code}{EASYFIND_EXTENSION}"


def read_nds_rom_identity(rom_path: str | Path) -> tuple[str, str]:
    """Read (game_code, title) from a Nintendo DS ROM header."""
    from ..nds import NDSRom

    rom = NDSRom.from_path(str(rom_path))
    return normalize_game_code(rom.info.game_code), rom.info.title.strip()
