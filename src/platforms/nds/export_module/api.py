"""JSON-facing Nintendo DS tile discovery and export API.

This is deliberately independent from Qt and the extractor modal. Automation,
LLM tools, and future local HTTP adapters can discover exact spatial
occurrences first, then export selected candidate IDs through the same `.tile`
bundle writer used by RAE's UI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from ....core.assets import Asset
from ..gltf.extract import (
    MaterialComponent,
    choose_logical_tile_anchor,
    group_logical_tile_materials,
    list_material_components,
    logical_tile_family,
    shoreline_tile_occurrences,
    spatial_assembly_materials,
    spatial_tile_occurrences,
    suggest_tile_surface_origin_y,
    tile_feature_kind,
)
from .tile_extract import PreviewTileBatchItem, export_preview_material_occurrences_as_tiles

API_VERSION = 1


@dataclass(frozen=True)
class TileExportCandidate:
    """One source-authored spatial occurrence available for `.tile` export."""

    candidate_id: str
    name: str
    family: str
    shape: str
    selected_materials: tuple[str, ...]
    spatial_tile_bounds: tuple[float, float, float, float]
    footprint: tuple[int, int]
    origin_y: float | None
    source_occurrence: int

    def to_api_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        return {
            "id": payload.pop("candidate_id"),
            "name": payload.pop("name"),
            "family": payload.pop("family"),
            "shape": payload.pop("shape"),
            "materials": list(payload.pop("selected_materials")),
            "bounds": list(payload.pop("spatial_tile_bounds")),
            "footprint": {
                "width": payload.pop("footprint")[0],
                "height": self.footprint[1],
            },
            "originY": payload.pop("origin_y"),
            "sourceOccurrence": payload.pop("source_occurrence"),
        }


def tile_export_api_schema() -> dict[str, Any]:
    """Return the compact serializable contract used by automation clients."""
    return {
        "apiVersion": API_VERSION,
        "platform": "nds",
        "outputFormat": "pokemon_resort.tile",
        "actions": {
            "discover": {
                "required": ["sourceGlb"],
                "optional": ["materials", "tileSize"],
            },
            "export": {
                "required": ["sourceGlb", "outputDir"],
                "optional": [
                    "materials",
                    "candidateIds",
                    "tileSize",
                    "asset",
                    "materialSpecs",
                ],
            },
        },
        "notes": [
            "Call discover before export and select stable candidate IDs.",
            "candidateIds may be omitted or set to ['*'] to export every discovered occurrence.",
            "Exported bundles are directly importable by Pokemon Resort Admin.",
        ],
    }


def _slug(value: str, fallback: str = "tile") -> str:
    result = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_")
    return result or fallback


def _resolve_materials(
    available: Sequence[str],
    requested: Sequence[str],
) -> tuple[str, ...]:
    by_key = {name.casefold(): name for name in available}
    resolved: list[str] = []
    missing: list[str] = []
    for raw in requested:
        value = str(raw or "").strip()
        match = by_key.get(value.casefold())
        if match is None:
            missing.append(value)
        elif match not in resolved:
            resolved.append(match)
    if missing:
        preview = ", ".join(available[:24])
        raise ValueError(
            f"Unknown material(s): {', '.join(missing)}. Available materials begin: {preview}"
        )
    return tuple(resolved)


def _material_groups(
    available: Sequence[str],
    requested: Sequence[str],
) -> tuple[tuple[str, ...], ...]:
    if not requested:
        return group_logical_tile_materials(tuple(available))

    resolved = _resolve_materials(available, requested)
    families = {
        family
        for material in resolved
        if (family := logical_tile_family(material)) is not None
    }
    if len(families) == 1 and all(
        logical_tile_family(material) in families for material in resolved
    ):
        family = next(iter(families))
        return (
            tuple(material for material in available if logical_tile_family(material) == family),
        )
    return (resolved,)


def _occurrence_bounds(component: MaterialComponent) -> tuple[float, float, float, float]:
    return (
        float(component.bounds_min[0]),
        float(component.bounds_min[2]),
        float(component.bounds_max[0]),
        float(component.bounds_max[2]),
    )


def _footprint(
    bounds: tuple[float, float, float, float],
    tile_size: float,
) -> tuple[int, int]:
    min_x, min_z, max_x, max_z = bounds
    size = max(0.001, float(tile_size))
    return (
        max(1, round((max_x - min_x) / size)),
        max(1, round((max_z - min_z) / size)),
    )


def _candidate_id(
    source_glb: Path,
    family: str,
    materials: Sequence[str],
    bounds: Sequence[float],
) -> str:
    identity = json.dumps(
        {
            "source": str(source_glb.resolve()),
            "family": family,
            "materials": [value.casefold() for value in materials],
            "bounds": [round(float(value), 5) for value in bounds],
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def discover_tile_candidates(
    source_glb: Path,
    *,
    materials: Sequence[str] = (),
    tile_size: float = 16.0,
) -> list[TileExportCandidate]:
    """Discover exact source occurrences without any UI or preview state."""
    source_glb = Path(source_glb).expanduser().resolve()
    if not source_glb.is_file():
        raise FileNotFoundError(f"Source GLB does not exist: {source_glb}")

    raw_components = list_material_components(source_glb)
    if not raw_components:
        return []
    available = tuple(raw_components)
    component_lookup = {
        name.casefold(): tuple(components)
        for name, components in raw_components.items()
    }
    groups = _material_groups(available, tuple(materials))
    candidates: list[TileExportCandidate] = []
    source_stem = _slug(source_glb.stem, "nds_map")

    for group in groups:
        if not group:
            continue
        anchor_name = choose_logical_tile_anchor(component_lookup, group)
        anchor_components = component_lookup.get(anchor_name.casefold(), ())
        if not anchor_components:
            continue
        family = logical_tile_family(anchor_name) or f"material:{anchor_name.casefold()}"
        family_kind = family.split(":", 1)[0]
        if family_kind == "shore":
            occurrences = shoreline_tile_occurrences(anchor_components, tile_size=tile_size)
        elif family_kind == "puddle":
            occurrences = anchor_components
        else:
            # Match the extractor modal: repeated props infer their authored
            # spatial repeat from geometry/UVs instead of being forced into a
            # 16-unit crop (the old behavior split trees and grass clusters).
            occurrences = spatial_tile_occurrences(anchor_components)

        shape_counts: dict[str, int] = {}
        for occurrence_index, occurrence in enumerate(occurrences, start=1):
            bounds = _occurrence_bounds(occurrence)
            footprint = _footprint(bounds, tile_size)
            shape = "corner" if footprint[0] > 1 and footprint[1] > 1 else "straight"
            if footprint == (1, 1):
                shape = "cell"
            shape_key = f"{shape}_{footprint[0]}x{footprint[1]}"
            shape_counts[shape_key] = shape_counts.get(shape_key, 0) + 1
            assembled = spatial_assembly_materials(
                component_lookup,
                group,
                bounds,
                (float(occurrence.bounds_min[1]), float(occurrence.bounds_max[1])),
                feature_kind=tile_feature_kind(group),
            )
            origin_y = suggest_tile_surface_origin_y(component_lookup, assembled)
            family_slug = _slug(family.replace(":", "_"), "material")
            serial = shape_counts[shape_key]
            name = (
                f"{source_stem} {family_slug.replace('_', ' ')} "
                f"{shape} {serial:02d} ({footprint[0]}x{footprint[1]})"
            )
            candidates.append(
                TileExportCandidate(
                    candidate_id=_candidate_id(source_glb, family, assembled, bounds),
                    name=name,
                    family=family,
                    shape=shape,
                    selected_materials=tuple(assembled),
                    spatial_tile_bounds=bounds,
                    footprint=footprint,
                    origin_y=origin_y,
                    source_occurrence=occurrence_index,
                )
            )
    return candidates


def _asset_from_request(source_glb: Path, request: Mapping[str, Any]) -> Asset:
    details = request.get("asset")
    details = details if isinstance(details, Mapping) else {}
    virtual_path = str(details.get("virtualPath") or source_glb.name)
    asset_id = str(details.get("id") or f"nds-tile-api:{source_glb.stem}")
    return Asset(
        asset_id=asset_id,
        virtual_path=virtual_path,
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=b"",
        original_data=b"",
    )


def export_tile_candidates(
    source_glb: Path,
    candidates: Sequence[TileExportCandidate],
    output_dir: Path,
    *,
    asset: Asset | None = None,
    material_specs: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[Path]:
    """Export discovered candidates through RAE's canonical `.tile` writer."""
    source_glb = Path(source_glb).expanduser().resolve()
    output_dir = Path(output_dir).expanduser().resolve()
    export_asset = asset or _asset_from_request(source_glb, {})
    used_filenames: set[str] = set()
    items: list[PreviewTileBatchItem] = []
    for candidate in candidates:
        base = _slug(candidate.name, "tile")
        filename = f"{base}.tile"
        serial = 2
        while filename.casefold() in used_filenames:
            filename = f"{base}_{serial:02d}.tile"
            serial += 1
        used_filenames.add(filename.casefold())
        items.append(
            PreviewTileBatchItem(
                name=candidate.name,
                filename=filename,
                selected_materials=candidate.selected_materials,
                spatial_tile_bounds=candidate.spatial_tile_bounds,
                footprint=candidate.footprint,
                origin_y=candidate.origin_y,
            )
        )

    fallback_paths = sorted(source_glb.parent.glob("*.png"))
    return export_preview_material_occurrences_as_tiles(
        asset=export_asset,
        source_glb=source_glb,
        items=items,
        output_dir=output_dir,
        mesh_labels=[],
        mesh_texture_paths=[],
        texture_by_name={},
        fallback_paths=fallback_paths,
        material_to_texture={},
        texture_bind_order=[],
        material_specs={
            str(name): dict(spec)
            for name, spec in (material_specs or {}).items()
        },
    )


def handle_tile_export_request(request: Mapping[str, Any]) -> dict[str, Any]:
    """Execute one JSON-compatible discover/export request."""
    requested_version = int(request.get("apiVersion") or API_VERSION)
    if requested_version != API_VERSION:
        raise ValueError(
            f"Unsupported NDS tile export API version {requested_version}; expected {API_VERSION}."
        )
    action = str(request.get("action") or "discover").casefold()
    if action == "schema":
        return tile_export_api_schema()
    source_value = str(request.get("sourceGlb") or "").strip()
    if not source_value:
        raise ValueError("sourceGlb is required.")
    source_glb = Path(source_value).expanduser().resolve()
    raw_materials = request.get("materials") or ()
    if isinstance(raw_materials, str):
        raw_materials = [raw_materials]
    materials = tuple(str(value) for value in raw_materials)
    tile_size = float(request.get("tileSize") or 16.0)
    candidates = discover_tile_candidates(
        source_glb,
        materials=materials,
        tile_size=tile_size,
    )
    response: dict[str, Any] = {
        "apiVersion": API_VERSION,
        "platform": "nds",
        "action": action,
        "sourceGlb": str(source_glb),
        "candidateCount": len(candidates),
    }
    if action == "discover":
        response["candidates"] = [candidate.to_api_dict() for candidate in candidates]
        return response
    if action != "export":
        raise ValueError(f"Unknown action: {action}")

    output_value = str(request.get("outputDir") or "").strip()
    if not output_value:
        raise ValueError("outputDir is required for export.")
    selected_ids = request.get("candidateIds") or ["*"]
    if isinstance(selected_ids, str):
        selected_ids = [selected_ids]
    selected_keys = {str(value) for value in selected_ids}
    selected = (
        candidates
        if "*" in selected_keys
        else [candidate for candidate in candidates if candidate.candidate_id in selected_keys]
    )
    missing_ids = selected_keys - {"*"} - {candidate.candidate_id for candidate in selected}
    if missing_ids:
        raise ValueError(f"Unknown candidateIds: {', '.join(sorted(missing_ids))}")
    asset = _asset_from_request(source_glb, request)
    specs = request.get("materialSpecs")
    specs = specs if isinstance(specs, Mapping) else {}
    written = export_tile_candidates(
        source_glb,
        selected,
        Path(output_value),
        asset=asset,
        material_specs=specs,
    )
    response["exportedCandidateIds"] = [candidate.candidate_id for candidate in selected]
    response["exportedCount"] = len(written)
    response["files"] = [str(path) for path in written]
    response["outputFormat"] = "pokemon_resort.tile"
    return response


def _read_request(path_value: str) -> Mapping[str, Any]:
    if path_value == "-":
        payload = sys.stdin.read()
    else:
        payload = Path(path_value).expanduser().read_text(encoding="utf-8")
    request = json.loads(payload)
    if not isinstance(request, Mapping):
        raise ValueError("The request document must be a JSON object.")
    return request


def main(argv: Sequence[str] | None = None) -> int:
    """Run the API over a JSON request file or stdin and emit JSON stdout."""
    parser = argparse.ArgumentParser(description="RAE Nintendo DS tile export JSON API")
    parser.add_argument(
        "request",
        nargs="?",
        default="-",
        help="JSON request path, or '-' to read stdin (default)",
    )
    args = parser.parse_args(argv)
    try:
        response = handle_tile_export_request(_read_request(args.request))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "apiVersion": API_VERSION,
                    "platform": "nds",
                    "error": str(exc),
                    "errorType": type(exc).__name__,
                },
                indent=2,
            )
        )
        return 1
    print(json.dumps(response, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
