"""Deduplicated Generation V building GLB catalog export."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ....core.assets import Asset
from ..gltf.embed_textures import embed_glb_external_images
from ..gltf.extract import is_shadow_material, recenter_glb_geometry
from ..gltf.glb_io import read_glb
from ..gltf.merge_animations import scale_glb_skeletal_animation_durations
from ..nitro_names import extract_nitro_names


@dataclass
class _BuildingRecord:
    key: str
    model: object
    occurrences: list[dict[str, object]] = field(default_factory=list)
    bta0: dict[str, bytes] = field(default_factory=dict)
    patterns: dict[str, bytes] = field(default_factory=dict)


def _content_key(model_data: bytes, metadata: bytes) -> str:
    digest = hashlib.sha256()
    for payload in (model_data, metadata):
        digest.update(len(payload).to_bytes(8, "little"))
        digest.update(payload)
    return digest.hexdigest()


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "building"


def export_gen5_building_catalog(
    rom_path: Path,
    output_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Export every unique AB building model, including its animation resources.

    Identical model/metadata payloads repeated across interior and exterior
    packs are emitted once. The catalog retains every source occurrence.
    """
    from ..exporter import convert_with_apicula
    from ..map_objects import (
        _MODEL_ANIMATION_INFO,
        _gen5_building_archives,
        _narc_files,
        embedded_model_animation_resources,
        parse_ab_building_pack,
        parse_area_data,
    )
    from ..material_animation import attach_btp0_pattern_motion, attach_gen5_pattern_motion
    from ..rom import NDSRom

    rom_path = Path(rom_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rom = NDSRom.from_path(str(rom_path))
    rom_files = {item.path: item.data for item in rom.iter_files()}

    bta_archive = _narc_files(rom_files["a/0/6/8"], "a/0/6/8") if "a/0/6/8" in rom_files else []
    pattern_archive = _narc_files(rom_files["a/0/6/9"], "a/0/6/9") if "a/0/6/9" in rom_files else []
    animation_ids: dict[tuple[bool, int], tuple[set[int], set[int]]] = defaultdict(lambda: (set(), set()))
    area_data = rom_files.get("a/0/1/3", b"")
    for area_index in range(len(area_data) // 12):
        area = parse_area_data(area_data, area_index)
        bta_ids, pattern_ids = animation_ids[(area.is_outside, area.building_pack)]
        if area.translate_animation != 0xFF:
            bta_ids.add(area.translate_animation)
        if area.sequential_animation != 0xFF:
            pattern_ids.add(area.sequential_animation)

    records: dict[str, _BuildingRecord] = {}
    texture_names: dict[str, set[str]] = {}
    for outside in (True, False):
        model_path, texture_path, model_packs, texture_packs = _gen5_building_archives(
            rom_files, outside=outside
        )
        for pack_index, payload in enumerate(model_packs):
            try:
                models = parse_ab_building_pack(payload)
            except ValueError:
                continue
            texture_data = texture_packs[pack_index] if pack_index < len(texture_packs) else b""
            texture_hash = hashlib.sha256(texture_data).hexdigest()
            if texture_hash not in texture_names:
                texture_names[texture_hash] = {
                    name.casefold() for name in extract_nitro_names(texture_data)
                }
            bta_ids, pattern_ids = animation_ids.get((outside, pack_index), (set(), set()))
            for model in models.values():
                key = _content_key(model.data, model.metadata)
                record = records.setdefault(key, _BuildingRecord(key=key, model=model))
                requested = {name.casefold() for name in extract_nitro_names(model.data)}
                record.occurrences.append(
                    {
                        "archive": model_path,
                        "textureArchive": texture_path,
                        "pack": pack_index,
                        "modelIndex": model.index,
                        "insideOutside": "outside" if outside else "inside",
                        "textureData": texture_data,
                        "textureScore": len(requested & texture_names[texture_hash]),
                    }
                )
                for animation_id in bta_ids:
                    if animation_id < len(bta_archive) and bta_archive[animation_id][:4] == b"BTA0":
                        data = bytes(bta_archive[animation_id])
                        record.bta0[hashlib.sha256(data).hexdigest()] = data
                for animation_id in pattern_ids:
                    if animation_id < len(pattern_archive):
                        data = bytes(pattern_archive[animation_id])
                        target = record.bta0 if data[:4] == b"BTA0" else record.patterns
                        target[hashlib.sha256(data).hexdigest()] = data

    written: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    ordered = sorted(records.values(), key=lambda item: (str(item.model.name).casefold(), item.key))
    with tempfile.TemporaryDirectory(prefix="rae_black2_buildings_") as temp_name:
        work_root = Path(temp_name)
        for position, record in enumerate(ordered, 1):
            model = record.model
            if progress:
                progress(f"Exporting building {position}/{len(ordered)}: {model.name}")
            occurrence = max(record.occurrences, key=lambda item: int(item["textureScore"]))
            filename = f"{_safe_name(str(model.name))}_{record.key[:10]}.glb"
            output_path = output_dir / filename
            animation_resources = embedded_model_animation_resources(model.metadata)
            try:
                if not output_path.is_file():
                    work_dir = work_root / record.key
                    model_asset = Asset(
                        asset_id=f"gen5-building-{record.key[:16]}",
                        virtual_path=f"{occurrence['archive']}#building_{model.index}_{model.name}.nsbmd",
                        kind="Model",
                        magic="BMD0",
                        extension=".nsbmd",
                        data=model.data,
                        original_data=model.data,
                    )
                    siblings = [Asset(
                        asset_id=f"gen5-building-texture-{record.key[:16]}",
                        virtual_path=f"{occurrence['textureArchive']}/file_{occurrence['pack']:04d}.nsbtx",
                        kind="Texture",
                        magic="BTX0",
                        extension=".nsbtx",
                        data=occurrence["textureData"],
                        original_data=occurrence["textureData"],
                    )]
                    for index, (magic, data) in enumerate(animation_resources):
                        kind, extension = _MODEL_ANIMATION_INFO[magic.encode("ascii")]
                        siblings.append(Asset(
                            asset_id=f"gen5-building-{record.key[:16]}-embedded-{index}",
                            virtual_path=f"embedded_{index}{extension}",
                            kind=kind,
                            magic=magic,
                            extension=extension,
                            data=data,
                            original_data=data,
                        ))
                    for index, data in enumerate(record.bta0.values()):
                        siblings.append(Asset(
                            asset_id=f"gen5-building-{record.key[:16]}-area-bta-{index}",
                            virtual_path=f"area_{index}.nsbta",
                            kind="Texture SRT animation",
                            magic="BTA0",
                            extension=".nsbta",
                            data=data,
                            original_data=data,
                        ))
                    result = convert_with_apicula(
                        model_asset,
                        work_dir,
                        sibling_assets=siblings,
                        output_format="glb",
                        more_textures=False,
                        all_animations=True,
                    )
                    source_glb = next(
                        (path for path in result.output_files if path.suffix.casefold() == ".glb" and path.is_file()),
                        None,
                    )
                    if not result.ok or source_glb is None:
                        raise RuntimeError(result.message or "apicula produced no GLB")
                    embedded = work_dir / "embedded.glb"
                    embed_glb_external_images(
                        read_glb(source_glb),
                        base_dir=source_glb.parent,
                        search_paths=[path for path in work_dir.iterdir() if path.is_file()],
                        require_all=True,
                    ).write(embedded)
                    scale_glb_skeletal_animation_durations(
                        read_glb(embedded), duration_scale=2.0
                    ).write(embedded)
                    btp_files = [data for magic, data in animation_resources if magic == "BTP0"]
                    if btp_files:
                        attach_btp0_pattern_motion(
                            embedded,
                            btp_files,
                            [path for path in work_dir.iterdir() if path.suffix.casefold() == ".png"],
                        )
                    for pattern in record.patterns.values():
                        attach_gen5_pattern_motion(embedded, pattern)
                    recenter_glb_geometry(embedded, output_path)

                document = read_glb(output_path).json
                materials = [str(item.get("name") or "") for item in document.get("materials") or []]
                rae = (document.get("extras") or {}).get("rae") or {}
                motion = rae.get("mapMaterialMotion") or {}
                written.append({
                    "file": filename,
                    "name": model.name,
                    "sha256": record.key,
                    "sourceOccurrences": [
                        {key: value for key, value in item.items() if key not in {"textureData", "textureScore"}}
                        for item in record.occurrences
                    ],
                    "embeddedAnimationResources": [magic for magic, _data in animation_resources],
                    "gltfAnimationClips": [
                        str(item.get("name") or f"animation_{index}")
                        for index, item in enumerate(document.get("animations") or [])
                    ],
                    "materialMotionClips": [
                        str(item.get("id") or item.get("name") or "material_motion")
                        for item in motion.get("clips") or []
                    ],
                    "shadowMaterials": [name for name in materials if is_shadow_material(name)],
                })
            except Exception as exc:
                errors.append({"name": model.name, "sha256": record.key, "error": str(exc)})

    report = {
        "rom": rom_path.name,
        "deduplication": "SHA-256 of exact BMD0 model and AB metadata payloads",
        "uniqueModelsDiscovered": len(records),
        "exportedGlbFiles": len(written),
        "modelsWithAnimations": sum(
            bool(item["gltfAnimationClips"] or item["materialMotionClips"])
            for item in written
        ),
        "modelsWithShadowMaterials": sum(bool(item["shadowMaterials"]) for item in written),
        "files": written,
        "errors": errors,
    }
    (output_dir / "building_catalog.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report
