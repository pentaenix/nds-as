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


def _glb_material_texture_paths(glb_path: Path) -> list[Path]:
    """PNG paths referenced by this GLB's material table (not every file in the folder)."""
    try:
        from ..platforms.nds.glb_preview_textures import parse_glb_material_texture_map
    except Exception:
        return []
    seen: set[str] = set()
    paths: list[Path] = []
    for path in parse_glb_material_texture_map(glb_path).values():
        if not path.is_file():
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        paths.append(path)
    return paths


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


def _seeds_for_mesh_part(
    mesh_label: str,
    *,
    mesh_texture_paths: list[Path | None],
    mesh_part_labels: list[str],
    material_to_texture: dict[str, str],
    assignments: dict[str, str],
    glb_path: Path | None,
) -> set[str]:
    seeds: set[str] = set()
    target = str(mesh_label or "").casefold()
    if not target:
        return seeds
    for index, label in enumerate(mesh_part_labels):
        if str(label).casefold() != target:
            continue
        if index < len(mesh_texture_paths):
            path = mesh_texture_paths[index]
            if path is not None and Path(path).is_file():
                seeds.update(_texture_seed_keys(Path(path)))
        break
    assigned = _assignment_for_mesh(assignments, mesh_label)
    if assigned:
        seeds.add(assigned)
    for mat_name, tex_name in material_to_texture.items():
        if str(mat_name).casefold() == target and tex_name:
            seeds.add(str(tex_name).casefold())
    if glb_path is not None and glb_path.is_file():
        try:
            from ..platforms.nds.glb_preview_textures import parse_glb_material_texture_map

            for mat_name, path in parse_glb_material_texture_map(glb_path).items():
                if str(mat_name).casefold() != target:
                    continue
                if path.is_file():
                    seeds.update(_texture_seed_keys(path))
        except Exception:
            pass
    return seeds


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


def relevant_texture_keys_for_mesh_part(
    mesh_label: str,
    *,
    fallback_paths: list[Path],
    texture_by_name: dict[str, Path],
    mesh_texture_paths: list[Path | None],
    material_to_texture: dict[str, str],
    assignments: dict[str, str],
    glb_path: Path | None = None,
    mesh_part_labels: list[str] | None = None,
) -> list[str]:
    """Texture keys relevant to one mesh part (same rules as the texture assigner)."""
    labels = list(mesh_part_labels or [])
    seeds = _seeds_for_mesh_part(
        mesh_label,
        mesh_texture_paths=mesh_texture_paths,
        mesh_part_labels=labels,
        material_to_texture=material_to_texture,
        assignments=assignments,
        glb_path=glb_path,
    )
    if not seeds:
        return []

    def _add_key(key: str) -> None:
        key = str(key or "").strip().casefold()
        if not key or key.isdigit() or key in seen:
            return
        seen.add(key)
        keys.append(key)

    keys: list[str] = []
    seen: set[str] = set()
    for path in _candidate_texture_paths(fallback_paths=fallback_paths, texture_by_name=texture_by_name):
        if not any(_matches_assigner_seed(path.stem, seed) for seed in seeds):
            continue
        _add_key(texture_key_for_path(path))
        stem = path.stem.casefold()
        if stem != texture_key_for_path(path):
            _add_key(stem)

    for raw_key, path in texture_by_name.items():
        path = Path(path) if path is not None else None
        if path is None or not path.is_file():
            continue
        path_key = texture_key_for_path(path)
        stem_key = path.stem.casefold()
        name_keys = {path_key, stem_key}
        raw = str(raw_key).casefold()
        if raw and not raw.isdigit() and raw not in name_keys:
            name_keys.add(raw)
        if not any(
            _matches_assigner_seed(candidate, seed) for candidate in name_keys for seed in seeds
        ):
            continue
        for candidate in name_keys:
            _add_key(candidate)

    return sorted(keys, key=_texture_key_sort_key)


def relevant_assigner_texture_paths(
    *,
    fallback_paths: list[Path],
    texture_by_name: dict[str, Path],
    mesh_texture_paths: list[Path | None],
    material_to_texture: dict[str, str],
    assignments: dict[str, str],
    glb_path: Path | None = None,
    mesh_part_labels: list[str] | None = None,
) -> list[Path]:
    """Textures to show in the assigner: active bindings + animation frames, not whole archives."""
    seeds: set[str] = set()
    active_materials = {label.casefold() for label in (mesh_part_labels or []) if label}

    for path in mesh_texture_paths:
        if path is not None and Path(path).is_file():
            seeds.update(_texture_seed_keys(Path(path)))

    if active_materials:
        for mat_name, tex_name in material_to_texture.items():
            if mat_name not in active_materials:
                continue
            if tex_name:
                seeds.add(tex_name.casefold())

    for tex_key in assignments.values():
        if tex_key:
            seeds.add(tex_key.casefold())

    glb_textures: list[Path] = []
    if glb_path is not None and glb_path.is_file():
        glb_textures = _glb_material_texture_paths(glb_path)
        for path in glb_textures:
            seeds.update(_texture_seed_keys(path))

    candidate_paths = _candidate_texture_paths(
        fallback_paths=fallback_paths,
        texture_by_name=texture_by_name,
    )

    if not seeds:
        return glb_textures

    relevant: list[Path] = []
    seen: set[str] = set()
    for path in candidate_paths:
        if not any(_matches_assigner_seed(path.stem, seed) for seed in seeds):
            continue
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        relevant.append(path)
    return relevant


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
