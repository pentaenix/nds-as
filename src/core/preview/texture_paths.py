"""Format-agnostic texture path helpers (no platform imports)."""

from __future__ import annotations

from pathlib import Path

from ..texture_assignments import build_best_path_index


def texture_map_from_paths(paths: list[Path]) -> dict[str, Path]:
    return build_best_path_index(paths)
