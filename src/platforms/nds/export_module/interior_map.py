"""Export an exact Gen 5 interior shell with Pokemon Resort authoring metadata."""
from __future__ import annotations

import json
import math
import re
import hashlib
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import trimesh

from ..gltf.glb_io import read_glb
from ..gltf.extract import extract_material_primitives, interior_wall_cap_material_name

TILE_SIZE = 16.0
KIT_ROLES = {"floor", "wall", "entrance", "stairs", "window", "shadow"}


def classify_interior_material(name: str) -> str:
    """Return a conservative authoring role for one Nitro material name."""
    key = re.sub(r"[^a-z0-9]+", "", str(name or "").casefold())
    if key.startswith("raeinteriorwalltopblack"):
        return "detail"
    if "kage" in key or "shadow" in key:
        return "shadow"
    if "mado" in key or "window" in key:
        return "window"
    if "kaidan" in key or "step" in key or "stair" in key:
        return "stairs"
    if "genkan" in key or "entrance" in key:
        return "entrance"
    if "kabe" in key or "wall" in key:
        return "wall"
    if any(token in key for token in ("yuka", "floor", "carpet", "mat")):
        return "floor"
    return "detail"


def _material_name(geometry_name: str, geometry: trimesh.Trimesh) -> str:
    material = getattr(getattr(geometry, "visual", None), "material", None)
    return str(getattr(material, "name", "") or geometry_name or "material")


def _scene_triangles(path: Path) -> list[dict[str, Any]]:
    loaded = trimesh.load(path, force="scene", process=False)
    scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
    records: list[dict[str, Any]] = []
    for node_name in scene.graph.nodes_geometry:
        transform, geometry_name = scene.graph[node_name]
        geometry = scene.geometry.get(geometry_name)
        if not isinstance(geometry, trimesh.Trimesh) or not len(geometry.faces):
            continue
        vertices = trimesh.transform_points(np.asarray(geometry.vertices), transform)
        triangles = vertices[np.asarray(geometry.faces, dtype=np.int64)]
        edges_a = triangles[:, 1] - triangles[:, 0]
        edges_b = triangles[:, 2] - triangles[:, 0]
        cross = np.cross(edges_a, edges_b)
        lengths = np.linalg.norm(cross, axis=1)
        normals = np.zeros_like(cross)
        valid = lengths > 1e-8
        normals[valid] = cross[valid] / lengths[valid, None]
        records.append({
            "material": _material_name(geometry_name, geometry),
            "triangles": triangles,
            "normals": normals,
            "areas": lengths * 0.5,
        })
    if not records:
        raise ValueError(f"Interior GLB contains no triangle geometry: {path}")
    return records


def _dominant_floor_datum(records: list[dict[str, Any]]) -> float:
    weighted: dict[float, float] = defaultdict(float)
    for record in records:
        role = classify_interior_material(record["material"])
        if role not in {"floor", "entrance", "stairs"}:
            continue
        triangles = record["triangles"]
        normals = record["normals"]
        areas = record["areas"]
        for triangle, normal, area in zip(triangles, normals, areas):
            if normal[1] < 0.7:
                continue
            weighted[round(float(np.mean(triangle[:, 1])), 3)] += float(area)
    if not weighted:
        return 0.0
    return max(weighted.items(), key=lambda item: item[1])[0]


def _point_in_triangle_xz(point: tuple[float, float], triangle: np.ndarray) -> bool:
    p = np.asarray(point, dtype=np.float64)
    a = triangle[0, (0, 2)]
    b = triangle[1, (0, 2)]
    c = triangle[2, (0, 2)]
    v0, v1, v2 = c - a, b - a, p - a
    dot00, dot01 = float(v0 @ v0), float(v0 @ v1)
    dot02, dot11, dot12 = float(v0 @ v2), float(v1 @ v1), float(v1 @ v2)
    denominator = dot00 * dot11 - dot01 * dot01
    if abs(denominator) < 1e-9:
        return False
    u = (dot11 * dot02 - dot01 * dot12) / denominator
    v = (dot00 * dot12 - dot01 * dot02) / denominator
    return u >= -1e-5 and v >= -1e-5 and u + v <= 1.00001


def _height_in_triangle_xz(point: tuple[float, float], triangle: np.ndarray) -> float | None:
    """Interpolate a triangle's Y at an XZ point, or return None outside it."""
    p = np.asarray(point, dtype=np.float64)
    a = triangle[0, (0, 2)]
    b = triangle[1, (0, 2)]
    c = triangle[2, (0, 2)]
    denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
    if abs(float(denominator)) < 1e-9:
        return None
    wa = ((b[1] - c[1]) * (p[0] - c[0]) + (c[0] - b[0]) * (p[1] - c[1])) / denominator
    wb = ((c[1] - a[1]) * (p[0] - c[0]) + (a[0] - c[0]) * (p[1] - c[1])) / denominator
    wc = 1.0 - wa - wb
    if min(wa, wb, wc) < -1e-5:
        return None
    return float(wa * triangle[0, 1] + wb * triangle[1, 1] + wc * triangle[2, 1])


def _height_step(floor_triangles: np.ndarray) -> float:
    """Infer the DS vertical authoring quantum while ignoring tiny trim offsets."""
    levels = sorted({round(float(np.mean(triangle[:, 1])), 3) for triangle in floor_triangles})
    differences = [b - a for a, b in zip(levels, levels[1:]) if b - a >= 4.0]
    if not differences:
        return TILE_SIZE
    return float(max(1, round(min(differences))))


def _grid_metadata(records: list[dict[str, Any]], tile_size: float) -> dict[str, Any]:
    all_triangles = np.concatenate([record["triangles"] for record in records], axis=0)
    bounds_min = np.min(all_triangles.reshape(-1, 3), axis=0)
    bounds_max = np.max(all_triangles.reshape(-1, 3), axis=0)
    floor_records = [
        record for record in records
        if classify_interior_material(record["material"]) in {"floor", "entrance", "stairs"}
    ] or records
    floor_triangles = np.concatenate([record["triangles"] for record in floor_records], axis=0)
    floor_normals = np.concatenate([record["normals"] for record in floor_records], axis=0)
    floor_triangles = floor_triangles[floor_normals[:, 1] >= 0.7]
    if not len(floor_triangles):
        floor_triangles = np.concatenate([record["triangles"] for record in records], axis=0)
    floor_points = floor_triangles.reshape(-1, 3)
    origin_x = math.floor(float(np.min(floor_points[:, 0])) / tile_size) * tile_size
    origin_z = math.floor(float(np.min(floor_points[:, 2])) / tile_size) * tile_size
    width = max(1, math.ceil((float(np.max(floor_points[:, 0])) - origin_x) / tile_size))
    height = max(1, math.ceil((float(np.max(floor_points[:, 2])) - origin_z) / tile_size))

    walkable: list[list[int]] = []
    surface_heights: list[list[float | None]] = []
    floor_level_sets: list[list[list[float]]] = []
    samples = ((0.5, 0.5), (0.2, 0.2), (0.8, 0.2), (0.2, 0.8), (0.8, 0.8))
    for y in range(height):
        row: list[int] = []
        height_row: list[float | None] = []
        level_row: list[list[float]] = []
        for x in range(width):
            sample_heights: list[float] = []
            all_heights: list[float] = []
            for sx, sy in samples:
                point = (origin_x + (x + sx) * tile_size, origin_z + (y + sy) * tile_size)
                hits = [
                    value for triangle in floor_triangles
                    if (value := _height_in_triangle_xz(point, triangle)) is not None
                ]
                if hits:
                    sample_heights.append(max(hits))
                    all_heights.extend(hits)
            row.append(1 if sample_heights else 0)
            height_row.append(round(float(np.median(sample_heights)), 4) if sample_heights else None)
            level_row.append(sorted({round(value, 3) for value in all_heights}))
        walkable.append(row)
        surface_heights.append(height_row)
        floor_level_sets.append(level_row)

    used_heights = [value for row in surface_heights for value in row if value is not None]
    base_datum = min(used_heights) if used_heights else 0.0
    step = _height_step(floor_triangles)
    height_mask = [
        [0 if value is None else max(0, min(255, round((value - base_datum) / step))) for value in row]
        for row in surface_heights
    ]
    ambiguous = []
    for y, row in enumerate(floor_level_sets):
        for x, levels in enumerate(row):
            if levels and levels[-1] - levels[0] > step * 1.5:
                ambiguous.append({"tile": [x, y], "levels": levels})

    return {
        "bounds": {
            "min": [round(float(value), 4) for value in bounds_min],
            "max": [round(float(value), 4) for value in bounds_max],
        },
        "gridOrigin": [round(origin_x, 4), round(origin_z, 4)],
        "gridSize": [width, height],
        "walkableMask": walkable,
        "blockedMask": [[0 if cell else 1 for cell in row] for row in walkable],
        "floorDatum": round(float(base_datum), 4),
        "heightStep": step,
        "heightMask": height_mask,
        "surfaceHeightMask": surface_heights,
        "ambiguousFloorCells": ambiguous,
    }


def _entrance_metadata(
    records: list[dict[str, Any]], grid: dict[str, Any], tile_size: float
) -> dict[str, Any]:
    origin_x, origin_z = grid["gridOrigin"]
    width, height = grid["gridSize"]
    entrance_records = [
        record for record in records if classify_interior_material(record["material"]) == "entrance"
    ]
    if entrance_records:
        points = np.concatenate([record["triangles"] for record in entrance_records], axis=0).reshape(-1, 3)
        center_x = float(np.mean([np.min(points[:, 0]), np.max(points[:, 0])]))
        center_z = float(np.mean([np.min(points[:, 2]), np.max(points[:, 2])]))
        tile_x = min(width - 1, max(0, math.floor((center_x - origin_x) / tile_size)))
        tile_y = min(height - 1, max(0, math.floor((center_z - origin_z) / tile_size)))
        distances = {
            "north": tile_y,
            "east": width - 1 - tile_x,
            "south": height - 1 - tile_y,
            "west": tile_x,
        }
        edge = min(distances, key=distances.get)
    else:
        edge, tile_x, tile_y = "south", width // 2, height - 1

    inward = {"north": (0, 1), "east": (-1, 0), "south": (0, -1), "west": (1, 0)}[edge]
    outward = (-inward[0], -inward[1])
    arrival = [
        min(width - 1, max(0, tile_x + inward[0])),
        min(height - 1, max(0, tile_y + inward[1])),
    ]
    return {
        "edge": edge,
        "tile": [tile_x, tile_y],
        "arrivalTile": arrival,
        "returnTriggerTile": [tile_x + outward[0], tile_y + outward[1]],
        "arrivalFacing": {"north": "south", "east": "west", "south": "north", "west": "east"}[edge],
        "exitDirection": {"north": "north", "east": "east", "south": "south", "west": "west"}[edge],
    }


def _inset_boundary_collision(grid: dict[str, Any], entrance: dict[str, Any]) -> None:
    """Keep actors one tile inside authored walls while leaving the doorway usable."""
    width, height = grid["gridSize"]
    if width < 3 or height < 3:
        return
    blocked = [list(row) for row in grid["blockedMask"]]
    for x in range(width):
        blocked[0][x] = 1
        blocked[height - 1][x] = 1
    for y in range(height):
        blocked[y][0] = 1
        blocked[y][width - 1] = 1
    tile = entrance.get("tile") or []
    if len(tile) >= 2:
        x, y = int(tile[0]), int(tile[1])
        if 0 <= x < width and 0 <= y < height:
            blocked[y][x] = 0
    grid["blockedMask"] = blocked
    grid["collisionInsetTiles"] = 1


def build_gen5_interior_metadata(
    source_glb: str | Path,
    *,
    map_file_index: int,
    area_index: int,
    virtual_path: str,
    rom_name: str,
    tile_size: float = TILE_SIZE,
) -> dict[str, Any]:
    path = Path(source_glb)
    records = _scene_triangles(path)
    grid = _grid_metadata(records, tile_size)
    primary_floor_datum = _dominant_floor_datum(records)
    roles: dict[str, list[str]] = defaultdict(list)
    for record in records:
        name = str(record["material"])
        role = classify_interior_material(name)
        if name not in roles[role]:
            roles[role].append(name)
    entrance = _entrance_metadata(records, grid, tile_size)
    _inset_boundary_collision(grid, entrance)
    return {
        "format": "rae.gen5Interior",
        "version": 1,
        "source": {
            "rom": rom_name,
            "virtualPath": virtual_path,
            "mapFileIndex": int(map_file_index),
            "areaDataIndex": int(area_index),
        },
        "tileSize": float(tile_size),
        "primaryFloorDatum": primary_floor_datum,
        **grid,
        "materialRoles": {key: sorted(values) for key, values in sorted(roles.items())},
        "entrance": entrance,
    }


def export_gen5_interior_package(
    composition: Any,
    output_dir: str | Path,
    *,
    rom_name: str = "",
    virtual_path: str = "",
) -> tuple[Path, Path]:
    """Write a self-contained terrain shell GLB and matching metadata sidecar."""
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    objects = composition.objects
    stem = f"map_{int(objects.map_file_index):04d}_interior"
    glb_path = output / f"{stem}.glb"
    metadata_path = output / f"{stem}.interior.json"
    metadata = build_gen5_interior_metadata(
        composition.terrain_glb,
        map_file_index=objects.map_file_index,
        area_index=objects.area.index,
        virtual_path=virtual_path or f"a/0/0/8/file_{int(objects.map_file_index):04d}.bin",
        rom_name=rom_name,
    )
    source_glb = read_glb(composition.terrain_glb)
    material_names = [
        str(material.get("name") or f"material_{index}")
        for index, material in enumerate(source_glb.json.get("materials") or [])
        if isinstance(material, dict)
    ]
    wall_materials = {
        material for material in material_names
        if classify_interior_material(material) == "wall"
    }
    entrance_edge = str((metadata.get("entrance") or {}).get("edge") or "").casefold()
    grid_origin = metadata.get("gridOrigin") or [0.0, 0.0]
    grid_size = metadata.get("gridSize") or [1, 1]
    tile_size = float(metadata.get("tileSize") or TILE_SIZE)
    cutaway_coordinate = {
        "north": float(grid_origin[1]) + tile_size,
        "south": float(grid_origin[1]) + (int(grid_size[1]) - 1) * tile_size,
        "west": float(grid_origin[0]) + tile_size,
        "east": float(grid_origin[0]) + (int(grid_size[0]) - 1) * tile_size,
    }.get(entrance_edge)
    extract_material_primitives(
        Path(composition.terrain_glb),
        glb_path,
        material_names,
        vertical_faces_only_materials=wall_materials,
        black_wall_cap_materials=wall_materials,
        cutaway_edge=entrance_edge or None,
        cutaway_coordinate=cutaway_coordinate,
    )
    glb = read_glb(glb_path)
    extras = glb.json.setdefault("extras", {})
    rae = extras.setdefault("rae", {})
    rae["interiorMap"] = metadata
    glb.write(glb_path)
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    return glb_path, metadata_path


def is_gen5_interior_candidate(source_glb: str | Path) -> bool:
    """Conservatively identify terrain containing both authored floors and walls."""
    roles = {
        classify_interior_material(str(material.get("name") or ""))
        for material in (read_glb(Path(source_glb)).json.get("materials") or [])
        if isinstance(material, dict)
    }
    return "floor" in roles and "wall" in roles


def _safe_part_name(role: str, material: str) -> str:
    stem = re.sub(r"[^a-z0-9_-]+", "_", material.casefold()).strip("_") or "material"
    digest = hashlib.sha1(material.encode("utf-8")).hexdigest()[:8]
    return f"{role}_{stem}_{digest}"


def export_gen5_interior_kit(
    source_glb: str | Path,
    metadata: dict[str, Any],
    output_dir: str | Path,
) -> tuple[Path, tuple[Path, ...]]:
    """Split an exact interior shell into reusable material-group GLBs.

    Geometry buffers, UVs, materials, embedded textures, and material motion are
    retained by the glTF subsetter. Each part is recentered with a reversible
    source placement recorded in the manifest.
    """
    source = Path(source_glb)
    output = Path(output_dir)
    parts_dir = output / "parts"
    parts_dir.mkdir(parents=True, exist_ok=True)
    glb = read_glb(source)
    materials = [
        str(material.get("name") or f"material_{index}")
        for index, material in enumerate(glb.json.get("materials") or [])
        if isinstance(material, dict)
    ]
    floor_datum = float(metadata.get("floorDatum") or 0.0)
    origin_x, origin_z = metadata.get("gridOrigin") or [0.0, 0.0]
    written: list[Path] = []
    manifest_parts: list[dict[str, Any]] = []
    for material in materials:
        role = classify_interior_material(material)
        if role not in KIT_ROLES:
            continue
        part_id = _safe_part_name(role, material)
        part_path = parts_dir / f"{part_id}.glb"
        cap_material = interior_wall_cap_material_name(material)
        source_has_cap = cap_material in materials
        extract_material_primitives(
            source,
            part_path,
            [material, cap_material] if role == "wall" and source_has_cap else [material],
            recenter=True,
            origin_y=floor_datum,
            vertical_faces_only_materials={material} if role == "wall" else None,
            black_wall_cap_materials={material} if role == "wall" and not source_has_cap else None,
            cutaway_edge=str((metadata.get("entrance") or {}).get("edge") or "") or None,
            cutaway_coordinate={
                "north": float((metadata.get("gridOrigin") or [0.0, 0.0])[1]) + TILE_SIZE,
                "south": float((metadata.get("gridOrigin") or [0.0, 0.0])[1])
                    + (int((metadata.get("gridSize") or [1, 1])[1]) - 1) * TILE_SIZE,
                "west": float((metadata.get("gridOrigin") or [0.0, 0.0])[0]) + TILE_SIZE,
                "east": float((metadata.get("gridOrigin") or [0.0, 0.0])[0])
                    + (int((metadata.get("gridSize") or [1, 1])[0]) - 1) * TILE_SIZE,
            }.get(str((metadata.get("entrance") or {}).get("edge") or "")),
        )
        part = read_glb(part_path)
        bounds = (((part.json.get("extras") or {}).get("rae") or {}).get("tileBounds") or {})
        minimum = bounds.get("min") or [0.0, floor_datum, 0.0]
        maximum = bounds.get("max") or minimum
        center_x = (float(minimum[0]) + float(maximum[0])) * 0.5
        center_z = (float(minimum[2]) + float(maximum[2])) * 0.5
        footprint = [
            max(1, math.ceil((float(maximum[0]) - float(minimum[0])) / TILE_SIZE)),
            max(1, math.ceil((float(maximum[2]) - float(minimum[2])) / TILE_SIZE)),
        ]
        record = {
            "id": part_id,
            "role": role,
            "sourceMaterial": material,
            "glb": f"parts/{part_path.name}",
            "sourceBounds": {"min": minimum, "max": maximum},
            "mapPlacement": [round(center_x - float(origin_x), 4), 0.0, round(center_z - float(origin_z), 4)],
            "footprint": footprint,
            "collisionHint": "blocked" if role in {"wall", "window"} else "walkable",
        }
        part.json.setdefault("extras", {}).setdefault("rae", {})["interiorKitPart"] = {
            **record,
            "source": metadata.get("source") or {},
        }
        part.write(part_path)
        written.append(part_path)
        manifest_parts.append(record)

    if not manifest_parts:
        raise ValueError("Interior shell contains no reusable floor, wall, entrance, stair, window, or shadow materials.")
    manifest = {
        "format": "rae.gen5InteriorKit",
        "version": 1,
        "source": metadata.get("source") or {},
        "tileSize": float(metadata.get("tileSize") or TILE_SIZE),
        "gridOrigin": metadata.get("gridOrigin") or [0.0, 0.0],
        "floorDatum": floor_datum,
        "componentPolicy": "material-group",
        "parts": manifest_parts,
    }
    manifest_path = output / "interior-kit.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    archive_path = output / "interior-kit.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.write(manifest_path, "interior-kit.json")
        for path in written:
            archive.write(path, f"parts/{path.name}")
    return manifest_path, tuple(written)
