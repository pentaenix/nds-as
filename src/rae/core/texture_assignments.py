"""Per-model manual texture assignments stored in RAE session files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

SESSION_KEY = "texture_assignments"


def empty_assignments() -> dict[str, dict[str, str]]:
    return {}


def load_texture_assignments(manifest: dict[str, Any] | None) -> dict[str, dict[str, str]]:
    """Return asset_id → {mesh_label: texture_key} from a session manifest."""
    if not manifest:
        return empty_assignments()
    raw = manifest.get(SESSION_KEY)
    if not isinstance(raw, dict):
        return empty_assignments()
    out: dict[str, dict[str, str]] = {}
    for asset_id, mapping in raw.items():
        if not isinstance(mapping, dict):
            continue
        cleaned: dict[str, str] = {}
        for mesh, tex in mapping.items():
            mesh_key = str(mesh).strip()
            tex_key = str(tex).strip()
            if mesh_key and tex_key:
                cleaned[mesh_key] = tex_key
        if cleaned:
            out[str(asset_id)] = cleaned
    return out


def texture_key_for_path(path: Path) -> str:
    stem = path.stem.casefold()
    return stem.split("__", 1)[0] or stem


def image_pixel_area(path: Path) -> int:
    try:
        from PIL import Image

        with Image.open(path) as img:
            return int(img.size[0]) * int(img.size[1])
    except Exception:
        return 0


def build_best_path_index(paths: list[Path]) -> dict[str, Path]:
    """Map texture keys to the highest-resolution PNG available."""
    out: dict[str, Path] = {}
    best_area: dict[str, int] = {}
    for path in paths:
        if not path.is_file():
            continue
        area = image_pixel_area(path)
        for key in {path.stem.casefold(), texture_key_for_path(path)}:
            if key not in out or area > best_area.get(key, 0):
                out[key] = path
                best_area[key] = area
    return out


def estimate_assignments_from_paths(
    mesh_labels: list[str],
    mesh_paths: list[Path | None],
) -> dict[str, str]:
    """Best-guess mesh→texture map from automatic preview path assignment."""
    out: dict[str, str] = {}
    for label, path in zip(mesh_labels, mesh_paths):
        if not label or path is None:
            continue
        out[label] = texture_key_for_path(path)
    return out


def resolve_assignment_paths(
    assignments: dict[str, str],
    *,
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
) -> dict[str, Path]:
    """Turn stored texture keys into concrete PNG paths for preview."""
    all_paths = list(fallback_paths) + [Path(p) for p in texture_by_name.values()]
    stem_map = build_best_path_index(all_paths)
    for key, path in texture_by_name.items():
        area = image_pixel_area(path)
        cur = stem_map.get(key.casefold())
        if cur is None or area > image_pixel_area(cur):
            stem_map[key.casefold()] = Path(path)

    out: dict[str, Path] = {}
    for mesh_label, tex_key in assignments.items():
        key = tex_key.casefold().strip()
        path = stem_map.get(key) or texture_by_name.get(key)
        if path is not None and Path(path).is_file():
            out[mesh_label] = Path(path)
    return out
