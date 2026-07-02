"""Canonical on-disk texture dictionary index paths keyed by Nintendo DS game code."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..easyfind.paths import is_valid_game_code, normalize_game_code
from ..install import project_root

TEXTURE_INDEX_DIR_NAME = "texture_index"
TEXTURE_INDEX_EXTENSION = ".texture-index"


def texture_index_store_dir(root: Path | None = None) -> Path:
    """Return the repo-local texture index folder (created on demand)."""
    base = root or project_root()
    path = base / TEXTURE_INDEX_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def texture_index_path_for_game_code(game_code: str, *, root: Path | None = None) -> Path:
    """Return the canonical texture-index path for a DS game code."""
    code = normalize_game_code(game_code)
    if not is_valid_game_code(code):
        raise ValueError(f"Invalid Nintendo DS game code: {game_code!r}")
    return texture_index_store_dir(root) / f"{code}{TEXTURE_INDEX_EXTENSION}"


def rom_content_sha256(rom_path: str | Path) -> str:
    """Return the SHA-256 hex digest of a ROM file."""
    digest = hashlib.sha256()
    with open(rom_path, "rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
