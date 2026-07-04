"""NDS texture assigner — GLB-aware path resolution (island-owned)."""

from __future__ import annotations

from pathlib import Path

from ...core.texture_assignments import (
    _assignment_for_mesh,
    _candidate_texture_paths,
    _matches_assigner_seed,
    _texture_key_sort_key,
    _texture_seed_keys,
    texture_key_for_path,
)


def _glb_material_texture_paths(glb_path: Path) -> list[Path]:
    """PNG paths referenced by this GLB's material table (not every file in the folder)."""
    try:
        from .gltf.preview_textures import parse_glb_material_texture_map
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
            from .gltf.preview_textures import parse_glb_material_texture_map

            for mat_name, path in parse_glb_material_texture_map(glb_path).items():
                if str(mat_name).casefold() != target:
                    continue
                if path.is_file():
                    seeds.update(_texture_seed_keys(path))
        except Exception:
            pass
    return seeds


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
    """Texture keys relevant to one mesh part (NDS assigner rules)."""
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
