"""Per-model manual texture assignments stored in RAE session files."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ANIM_FRAME_SUFFIX = re.compile(r"^(.+)[._](\d+)$", re.IGNORECASE)

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


def _texture_seed_keys(path: Path) -> set[str]:
    """Keys that identify a texture for assigner relevance (incl. palette base names)."""
    stem = path.stem.casefold()
    base = texture_key_for_path(path)
    seeds = {stem, base}
    match = _ANIM_FRAME_SUFFIX.match(base)
    if match:
        seeds.add(match.group(1).casefold())
    return seeds


def _matches_assigner_seed(stem: str, seed: str) -> bool:
    """True when a PNG stem is the seed or an animation frame variant (name.1, name_2, …)."""
    seed = seed.casefold().strip()
    if not seed:
        return False
    stem_cf = stem.casefold()
    base = stem_cf.split("__", 1)[0]
    if base == seed or stem_cf == seed:
        return True
    match = _ANIM_FRAME_SUFFIX.match(base)
    if match and match.group(1).casefold() == seed:
        return True
    suffix = base[len(seed) :]
    if suffix and suffix[0] in "._" and suffix[1:].isdigit():
        return base.startswith(seed)
    return False


def _assignment_for_mesh(assignments: dict[str, str], mesh_label: str) -> str:
    target = str(mesh_label or "").casefold()
    for mesh, tex in assignments.items():
        if str(mesh).casefold() == target:
            return str(tex).strip().casefold()
    return ""


def _texture_key_sort_key(key: str) -> tuple[int, str, int]:
    key = str(key or "").casefold()
    match = _ANIM_FRAME_SUFFIX.match(key)
    if match:
        return (0, match.group(1).casefold(), int(match.group(2)))
    return (1, key, 0)


def _candidate_texture_paths(
    *,
    fallback_paths: list[Path],
    texture_by_name: dict[str, Path],
) -> list[Path]:
    candidate_paths: list[Path] = []
    seen_candidates: set[str] = set()
    for path in list(fallback_paths) + list(texture_by_name.values()):
        path = Path(path)
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen_candidates:
            continue
        seen_candidates.add(key)
        candidate_paths.append(path)
    return candidate_paths


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
        # Keep palette/hash variants (e.g. boat_tex__hash) when parts bind different files.
        out[label] = Path(path).stem.casefold()
    return out


def _texture_paths_in_known_directories(paths: list[Path]) -> list[Path]:
    discovered: list[Path] = []
    seen_dirs: set[str] = set()
    for path in paths:
        parent = Path(path).resolve().parent
        dir_key = str(parent)
        if dir_key in seen_dirs or not parent.is_dir():
            continue
        seen_dirs.add(dir_key)
        for pattern in ("*.png", "*.PNG", "*.bmp", "*.BMP", "*.tga", "*.TGA"):
            discovered.extend(p for p in parent.glob(pattern) if p.is_file())
    return discovered


def resolve_assignment_paths(
    assignments: dict[str, str],
    *,
    texture_by_name: dict[str, Path],
    fallback_paths: list[Path],
) -> dict[str, Path]:
    """Turn stored texture keys into concrete PNG paths for preview."""
    all_paths = list(fallback_paths) + [Path(p) for p in texture_by_name.values()]
    discovered = _texture_paths_in_known_directories(all_paths)
    stem_map = build_best_path_index(list(dict.fromkeys([*all_paths, *discovered])))
    for key, path in texture_by_name.items():
        area = image_pixel_area(path)
        cur = stem_map.get(key.casefold())
        if cur is None or area > image_pixel_area(cur):
            stem_map[key.casefold()] = Path(path)

    out: dict[str, Path] = {}
    for mesh_label, tex_key in assignments.items():
        key = tex_key.casefold().strip()
        path = stem_map.get(key) or texture_by_name.get(key)
        if path is None:
            for candidate_key in (key, texture_key_for_path(Path(key))):
                path = stem_map.get(candidate_key)
                if path is not None:
                    break
        if path is not None and Path(path).is_file():
            out[mesh_label] = Path(path)
    return out
