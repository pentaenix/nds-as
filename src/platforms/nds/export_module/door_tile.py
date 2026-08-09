"""Pokemon Resort door tile export for discovered Gen 5 map doors."""
from __future__ import annotations

import json
import hashlib
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from ....core.assets import Asset
from ..gltf.extract import recenter_glb_geometry
from ..gltf.glb_io import read_glb
from .tile_bundle import _material_motion_animations, write_tile_archive

if TYPE_CHECKING:
    from ..map_objects import (
        Gen5MapComposition,
        Gen5MapObjectSet,
        Gen5MapPlacement,
        Gen5ObjectPreview,
        Gen5PlacedDoor,
    )


def door_animation_semantics(glb_path: Path) -> dict[str, object]:
    """Return stable open/close semantics without renaming source clips."""
    document = read_glb(glb_path).json
    clips = [
        str(animation.get("name") or f"animation_{index}")
        for index, animation in enumerate(document.get("animations") or [])
        if isinstance(animation, dict)
    ]
    def is_open(name: str) -> bool:
        key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        return "open" in key or key.endswith("_op") or key.endswith("_up")

    def is_close(name: str) -> bool:
        key = re.sub(r"[^a-z0-9]+", "_", name.casefold()).strip("_")
        return any(token in key for token in ("close", "shut")) or key.endswith("_cl") or key.endswith("_dn") or key.endswith("_down")

    open_clip = next((name for name in clips if is_open(name)), clips[0] if clips else "")
    close_clip = next(
        (name for name in clips if is_close(name)),
        "",
    )
    return {
        "clips": clips,
        "open": open_clip,
        "close": close_clip,
        "closeBehavior": "named" if close_clip else "reverse",
    }


def _safe_file_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", value).strip("._")
    return cleaned or "door"


def _export_door_record(
    *,
    door: "Gen5PlacedDoor",
    placement: "Gen5MapPlacement",
    objects: "Gen5MapObjectSet",
    source_glb: Path,
    output_path: Path,
) -> Path:
    semantics = door_animation_semantics(source_glb)
    model = door.model
    asset = Asset(
        asset_id=(
            f"gen5-map-{objects.map_file_index}-door-"
            f"{model.index}-placement-{placement.index}"
        ),
        virtual_path=(
            f"{objects.model_archive_path}/"
            f"file_{objects.area.building_pack:04d}.bin"
            f"#door_{model.index:02d}_{model.name}.nsbmd"
        ),
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=model.data,
        original_data=model.data,
    )
    output_path = Path(output_path)
    if output_path.suffix.casefold() != ".tile":
        output_path = output_path.with_suffix(".tile")
    with tempfile.TemporaryDirectory(prefix="rae_nds_door_tile_") as temp_name:
        staging = Path(temp_name)
        model_glb = recenter_glb_geometry(source_glb, staging / "door.glb")
        return write_tile_archive(
            output_path,
            model_glb=model_glb,
            asset=asset,
            animations=_material_motion_animations(model_glb, staging),
            staging=staging,
            name_override=model.name,
            source_details={
                "mapFileIndex": objects.map_file_index,
                "placementIndex": placement.index,
                "doorModelIndex": model.index,
                "doorDiscovery": door.source,
                "doorConfidence": door.confidence,
                **({"destinationZone": door.destination_zone} if door.destination_zone is not None else {}),
                **({"interiorFamily": door.interior_family} if door.interior_family else {}),
            },
            default_tags=["interaction.door"],
            default_properties={
                "interaction.kind": "door",
                "door.front": "south",
                "door.animation.phase": "trigger",
                "door.animation.open": str(semantics["open"]),
                "door.animation.close": str(semantics["closeBehavior"]),
                "door.animation.closeClip": str(semantics["close"]),
                "door.animation.clips": json.dumps(semantics["clips"], separators=(",", ":")),
                "door.source.rotationDegrees": placement.rotation_degrees,
                "door.source.offset": json.dumps(list(door.translation), separators=(",", ":")),
            },
        )


def export_discovered_door_tile(
    preview: "Gen5ObjectPreview",
    composition: "Gen5MapComposition",
    output_path: Path,
) -> Path:
    door = preview.door
    source_glb = preview.door_glb_path
    if door is None or source_glb is None:
        raise ValueError("The selected map placement has no discovered door model")
    return _export_door_record(
        door=door,
        placement=preview.placement,
        objects=composition.objects,
        source_glb=source_glb,
        output_path=output_path,
    )


def export_all_discovered_door_tiles(
    composition: "Gen5MapComposition",
    output_dir: Path,
) -> list[Path]:
    """Export every explicit or confidently inferred placed door in one map."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for preview in composition.previews:
        if preview.door is None or preview.door_glb_path is None:
            continue
        name = _safe_file_name(
            f"map_{composition.objects.map_file_index:04d}_placement_"
            f"{preview.placement.index:03d}_{preview.door.model.name}"
        )
        written.append(export_discovered_door_tile(preview, composition, output_dir / f"{name}.tile"))
    return written


def _convert_door_glb(objects: "Gen5MapObjectSet", door: "Gen5PlacedDoor", work_dir: Path) -> Path:
    from ..exporter import convert_with_apicula
    from ..gltf.embed_textures import embed_glb_external_images
    from ..gltf.merge_animations import scale_glb_skeletal_animation_durations
    from ..map_objects import _MODEL_ANIMATION_INFO, embedded_model_animation_resources

    model = door.model
    model_asset = Asset(
        asset_id=f"gen5-map-{objects.map_file_index}-door-{model.index}",
        virtual_path=(
            f"{objects.model_archive_path}/file_{objects.area.building_pack:04d}.bin"
            f"#door_{model.index:02d}_{model.name}.nsbmd"
        ),
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=model.data,
        original_data=model.data,
    )
    texture_asset = Asset(
        asset_id=f"gen5-door-textures-{objects.area.building_pack}",
        virtual_path=f"{objects.texture_archive_path}/file_{objects.area.building_pack:04d}.bin.nsbtx",
        kind="Texture",
        magic="BTX0",
        extension=".nsbtx",
        data=objects.texture_data,
        original_data=objects.texture_data,
    )
    siblings = [texture_asset]
    btp_payloads: list[bytes] = []
    area_animations = [
        *([objects.material_animation_data] if objects.material_animation_data else []),
        *objects.additional_material_animation_data,
    ]
    for index, payload in enumerate(area_animations):
        siblings.append(Asset(
            asset_id=f"gen5-map-{objects.map_file_index}-area-animation-{index}",
            virtual_path=f"area_bta_{index:02d}.nsbta",
            kind="Texture SRT animation",
            magic="BTA0",
            extension=".nsbta",
            data=payload,
            original_data=payload,
        ))
    for index, (magic, payload) in enumerate(embedded_model_animation_resources(model.metadata)):
        kind, extension = _MODEL_ANIMATION_INFO[magic.encode("ascii")]
        siblings.append(Asset(
            asset_id=f"gen5-door-{model.index}-animation-{index}",
            virtual_path=f"door_{model.index:02d}_animation_{index}{extension}",
            kind=kind,
            magic=magic,
            extension=extension,
            data=payload,
            original_data=payload,
        ))
        if magic == "BTP0":
            btp_payloads.append(payload)

    work_dir.mkdir(parents=True, exist_ok=True)
    result = convert_with_apicula(
        model_asset,
        work_dir,
        sibling_assets=siblings,
        output_format="glb",
        more_textures=False,
        all_animations=True,
    )
    glb = next((path for path in result.output_files if path.suffix.casefold() == ".glb" and path.is_file()), None)
    if not result.ok or glb is None:
        raise RuntimeError(result.message or f"Could not convert door model {model.index}: {model.name}")
    embedded = work_dir / f"{_safe_file_name(model.name)}_embedded.glb"
    embed_glb_external_images(
        read_glb(glb),
        base_dir=glb.parent,
        search_paths=[path for path in work_dir.iterdir() if path.is_file()],
        require_all=True,
    ).write(embedded)
    scale_glb_skeletal_animation_durations(read_glb(embedded), duration_scale=2.0).write(embedded)
    if btp_payloads:
        from ..material_animation import attach_btp0_pattern_motion

        attach_btp0_pattern_motion(
            embedded,
            btp_payloads,
            [path for path in work_dir.iterdir() if path.suffix.casefold() == ".png"],
        )
    return embedded


def export_all_gen5_rom_doors(
    rom_path: Path,
    output_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Discover and export every usable door occurrence from one Gen 5 ROM."""
    from ..map_objects import _narc_files, embedded_model_animation_resources, resolve_gen5_map_objects
    from ..rom import NDSRom

    rom_path = Path(rom_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rom = NDSRom.from_path(str(rom_path))
    rom_files = {item.path: item.data for item in rom.iter_files()}
    map_payloads = _narc_files(rom_files["a/0/0/8"], "a/0/0/8")
    written: list[Path] = []
    errors: list[dict[str, object]] = []
    discovered = 0
    with tempfile.TemporaryDirectory(prefix="rae_all_gen5_doors_") as temp_name:
        conversion_root = Path(temp_name)
        conversion_cache: dict[str, Path] = {}
        for map_index in range(len(map_payloads)):
            if progress and (map_index % 25 == 0 or map_index + 1 == len(map_payloads)):
                progress(f"Scanning map {map_index + 1}/{len(map_payloads)}")
            try:
                objects = resolve_gen5_map_objects(
                    rom_path,
                    f"a/0/0/8/file_{map_index:04d}.bin#terrain.nsbmd",
                    map_index_override=map_index,
                    _rom_files=rom_files,
                )
            except Exception as exc:
                errors.append({"mapFileIndex": map_index, "stage": "discover", "error": str(exc)})
                continue
            if not objects.doors:
                continue
            placements = {placement.index: placement for placement in objects.placements}
            for door in objects.doors:
                placement = placements.get(door.placement_index)
                if placement is None:
                    continue
                discovered += 1
                cache_key = hashlib.sha256(
                    door.model.data + objects.texture_data + b"".join(
                        payload for _magic, payload in embedded_model_animation_resources(door.model.metadata)
                    )
                ).hexdigest()
                try:
                    source_glb = conversion_cache.get(cache_key)
                    if source_glb is None:
                        source_glb = _convert_door_glb(
                            objects,
                            door,
                            conversion_root / cache_key,
                        )
                        conversion_cache[cache_key] = source_glb
                    name = _safe_file_name(
                        f"map_{map_index:04d}_placement_{placement.index:03d}_"
                        f"{door.model.name}_{door.source}"
                    )
                    written.append(_export_door_record(
                        door=door,
                        placement=placement,
                        objects=objects,
                        source_glb=source_glb,
                        output_path=output_dir / f"{name}.tile",
                    ))
                except Exception as exc:
                    errors.append({
                        "mapFileIndex": map_index,
                        "placementIndex": placement.index,
                        "doorModelIndex": door.model.index,
                        "stage": "export",
                        "error": str(exc),
                    })
    report = {
        "rom": rom_path.name,
        "mapCount": len(map_payloads),
        "discoveredDoorOccurrences": discovered,
        "exportedDoorTiles": len(written),
        "files": [path.name for path in written],
        "errors": errors,
    }
    (output_dir / "door_export_report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
