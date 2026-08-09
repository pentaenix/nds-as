"""Headless Pokemon Attend environment export profile for the 3DS island."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import struct
import tempfile
from typing import Callable, Iterable

from ..environment_catalog import (
    descriptor_for_attend_environment,
    selected_attend_environments,
)
from ..glbz import write_glbz
from ..service import build_model_glb

Progress = Callable[[str], None]


def _glb_json(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) < 20 or data[:4] != b"glTF":
        raise ValueError(f"not a GLB 2.0 file: {path}")
    json_length, json_type = struct.unpack_from("<II", data, 12)
    if json_type != 0x4E4F534A:
        raise ValueError(f"GLB has no JSON first chunk: {path}")
    return json.loads(data[20 : 20 + json_length].decode("utf-8"))


def _environment_report(glb_path: Path, glbz_path: Path, spec) -> dict:
    document = _glb_json(glb_path)
    rae = (document.get("extras") or {}).get("rae") or {}
    scene = rae.get("environmentScene") or {}
    motion = rae.get("mapMaterialMotion") or {}
    warnings: list[str] = []
    water_base_materials: list[str] = []
    water_overlay_materials: list[str] = []
    for material in document.get("materials") or []:
        material_rae = (material.get("extras") or {}).get("rae") or {}
        tev = material_rae.get("picaTev")
        if tev and tev.get("stages") and not tev.get("textureIndices"):
            warnings.append(f"{material.get('name', '<material>')}: TEV has no decoded texture units")
        role = (material_rae.get("environmentMaterial") or {}).get("role")
        if role == "water_base":
            water_base_materials.append(str(material.get("name") or "<material>"))
        elif role == "water_overlay":
            water_overlay_materials.append(str(material.get("name") or "<material>"))
    if water_overlay_materials and not water_base_materials:
        warnings.append(
            "water overlays have no base-water provider: "
            + ", ".join(water_overlay_materials)
        )
    return {
        "id": spec.id,
        "label": spec.label,
        "file": glbz_path.name,
        "sha256": hashlib.sha256(glbz_path.read_bytes()).hexdigest(),
        "bytes": glbz_path.stat().st_size,
        "sourceSlots": list(spec.slots),
        "compositionId": spec.composition_id or "",
        "route": spec.route,
        "timeStates": (scene.get("states") or {}).get("supportedTimes") or ["day"],
        "weatherStates": (scene.get("states") or {}).get("supportedWeather") or ["clear"],
        "animationClipCount": len(motion.get("clips") or []),
        "waterBaseMaterials": water_base_materials,
        "waterOverlayMaterials": water_overlay_materials,
        "warnings": warnings,
    }


def export_attend_environment_catalog(
    base_descriptor: dict,
    out_dir: str | Path,
    *,
    scene_ids: Iterable[str] | None = None,
    progress: Progress | None = None,
) -> list[Path]:
    """Export selected semantic environments plus a machine-readable report."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    reports: list[dict] = []
    for spec in selected_attend_environments(scene_ids):
        descriptor = descriptor_for_attend_environment(base_descriptor, spec.id)
        if progress:
            progress(f"3DS Attend: exporting {spec.label}…")
        with tempfile.TemporaryDirectory(prefix=f"rae_attend_{spec.id}_") as temp:
            glb_path = build_model_glb(descriptor, Path(temp), progress=progress)
            glbz_path = write_glbz(glb_path, out_dir / f"{spec.id}.glbz")
            reports.append(_environment_report(glb_path, glbz_path, spec))
            written.append(glbz_path)
    report_path = out_dir / "alola_attend_catalog.json"
    report_path.write_text(
        json.dumps(
            {
                "format": "rae.pokemon_attend_environment_catalog",
                "version": 1,
                "environments": reports,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    written.append(report_path)
    return written
