"""Models Resource-ready DAE packages for Generation V building archives."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from xml.etree import ElementTree

from ....core.assets import Asset
from ..nitro_names import extract_nitro_names
from .building_catalog import _content_key, _safe_name


@dataclass
class _DaeRecord:
    key: str
    model: object
    occurrences: list[dict[str, object]] = field(default_factory=list)
    bta0: dict[str, bytes] = field(default_factory=dict)


def _submission_name(source_name: str) -> str:
    words = re.sub(r"[_-]+", " ", source_name).strip().split()
    return " ".join(word.upper() if word.casefold() in {"pc", "gym"} else word.capitalize() for word in words)


def _write_submission_zip(zip_path: Path, files: list[Path]) -> None:
    """Write only accepted DAE/PNG files with stable, flat archive paths."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(files, key=lambda item: item.name.casefold()):
            info = zipfile.ZipInfo(path.name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def export_gen5_building_dae_packages(
    rom_path: Path,
    output_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Export one clean DAE+PNG ZIP per unique AB model payload."""
    from ..exporter import convert_with_apicula
    from ..map_objects import (
        _MODEL_ANIMATION_INFO,
        _gen5_building_archives,
        _narc_files,
        embedded_model_animation_resources,
        parse_ab_building_pack,
        parse_area_data,
    )
    from ..rom import NDSRom

    rom_path = Path(rom_path)
    output_dir = Path(output_dir)
    packages_dir = output_dir / "submission_zips"
    packages_dir.mkdir(parents=True, exist_ok=True)
    rom_files = {item.path: item.data for item in NDSRom.from_path(str(rom_path)).iter_files()}

    bta_archive = _narc_files(rom_files["a/0/6/8"], "a/0/6/8") if "a/0/6/8" in rom_files else []
    sequential_archive = (
        _narc_files(rom_files["a/0/6/9"], "a/0/6/9") if "a/0/6/9" in rom_files else []
    )
    bta_ids_by_pack: dict[tuple[bool, int], set[int]] = defaultdict(set)
    area_data = rom_files.get("a/0/1/3", b"")
    for area_index in range(len(area_data) // 12):
        area = parse_area_data(area_data, area_index)
        if area.translate_animation != 0xFF:
            bta_ids_by_pack[(area.is_outside, area.building_pack)].add(area.translate_animation)
        if area.sequential_animation != 0xFF:
            if area.sequential_animation < len(sequential_archive) and sequential_archive[area.sequential_animation][:4] == b"BTA0":
                # Negative ids distinguish the second archive while retaining set semantics.
                bta_ids_by_pack[(area.is_outside, area.building_pack)].add(-area.sequential_animation - 1)
    records: dict[str, _DaeRecord] = {}
    texture_name_cache: dict[str, set[str]] = {}
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
            if texture_hash not in texture_name_cache:
                texture_name_cache[texture_hash] = {
                    name.casefold() for name in extract_nitro_names(texture_data)
                }
            for model in models.values():
                key = _content_key(model.data, model.metadata)
                record = records.setdefault(key, _DaeRecord(key=key, model=model))
                requested = {name.casefold() for name in extract_nitro_names(model.data)}
                record.occurrences.append({
                    "archive": model_path,
                    "textureArchive": texture_path,
                    "pack": pack_index,
                    "modelIndex": model.index,
                    "insideOutside": "outside" if outside else "inside",
                    "textureData": texture_data,
                    "textureScore": len(requested & texture_name_cache[texture_hash]),
                })
                for animation_id in bta_ids_by_pack.get((outside, pack_index), set()):
                    source = (
                        sequential_archive[-animation_id - 1]
                        if animation_id < 0 and -animation_id - 1 < len(sequential_archive)
                        else bta_archive[animation_id]
                        if 0 <= animation_id < len(bta_archive)
                        else b""
                    )
                    if source[:4] == b"BTA0":
                        data = bytes(source)
                        record.bta0[hashlib.sha256(data).hexdigest()] = data

    written: list[dict[str, object]] = []
    errors: list[dict[str, str]] = []
    ordered = sorted(records.values(), key=lambda item: (str(item.model.name).casefold(), item.key))
    with tempfile.TemporaryDirectory(prefix="rae_models_resource_dae_") as temp_name:
        work_root = Path(temp_name)
        for position, record in enumerate(ordered, 1):
            model = record.model
            if progress:
                progress(f"Packaging DAE {position}/{len(ordered)}: {model.name}")
            occurrence = max(record.occurrences, key=lambda item: int(item["textureScore"]))
            package_name = f"{_safe_name(str(model.name))}_{record.key[:10]}_asset.zip"
            package_path = packages_dir / package_name
            embedded_animations = embedded_model_animation_resources(model.metadata)
            try:
                if not package_path.is_file():
                    work_dir = work_root / record.key
                    siblings = [Asset(
                        asset_id=f"texture-{record.key[:16]}",
                        virtual_path=f"{occurrence['textureArchive']}/file_{occurrence['pack']:04d}.nsbtx",
                        kind="Texture",
                        magic="BTX0",
                        extension=".nsbtx",
                        data=occurrence["textureData"],
                        original_data=occurrence["textureData"],
                    )]
                    for index, (magic, data) in enumerate(embedded_animations):
                        kind, extension = _MODEL_ANIMATION_INFO[magic.encode("ascii")]
                        siblings.append(Asset(
                            asset_id=f"animation-{record.key[:16]}-{index}",
                            virtual_path=f"animation_{index}{extension}",
                            kind=kind,
                            magic=magic,
                            extension=extension,
                            data=data,
                            original_data=data,
                        ))
                    for index, data in enumerate(record.bta0.values()):
                        siblings.append(Asset(
                            asset_id=f"area-animation-{record.key[:16]}-{index}",
                            virtual_path=f"area_animation_{index}.nsbta",
                            kind="Texture SRT animation",
                            magic="BTA0",
                            extension=".nsbta",
                            data=data,
                            original_data=data,
                        ))
                    model_asset = Asset(
                        asset_id=f"building-{record.key[:16]}",
                        virtual_path=f"{occurrence['archive']}#building_{model.index}_{model.name}.nsbmd",
                        kind="Model",
                        magic="BMD0",
                        extension=".nsbmd",
                        data=model.data,
                        original_data=model.data,
                    )
                    result = convert_with_apicula(
                        model_asset,
                        work_dir,
                        sibling_assets=siblings,
                        output_format="dae",
                        more_textures=False,
                        all_animations=True,
                    )
                    dae_files = sorted(path for path in result.output_files if path.suffix.casefold() == ".dae")
                    if not result.ok or not dae_files:
                        raise RuntimeError(result.message or "apicula produced no DAE")
                    accepted_files = dae_files + sorted(work_dir.glob("*.png"))
                    _write_submission_zip(package_path, accepted_files)

                with zipfile.ZipFile(package_path) as archive:
                    names = archive.namelist()
                    dae_names = [name for name in names if name.casefold().endswith(".dae")]
                    png_names = [name for name in names if name.casefold().endswith(".png")]
                    if len(dae_names) != 1 or len(dae_names) + len(png_names) != len(names):
                        raise ValueError("ZIP must contain exactly one DAE and only PNG texture companions")
                    root = ElementTree.fromstring(archive.read(dae_names[0]))
                    image_refs: list[str] = []
                    for library in root:
                        if library.tag.rsplit("}", 1)[-1] != "library_images":
                            continue
                        image_refs.extend(
                            str(node.text or "").strip()
                            for node in library.iter()
                            if node.tag.rsplit("}", 1)[-1] == "init_from"
                        )
                    missing = sorted({ref for ref in image_refs if ref and ref not in names})
                    if missing:
                        raise ValueError(f"DAE references missing texture(s): {', '.join(missing)}")
                    has_animation = any(
                        node.tag.rsplit("}", 1)[-1] in {"library_animations", "library_animation_clips"}
                        and list(node)
                        for node in root.iter()
                    )
                written.append({
                    "package": f"submission_zips/{package_name}",
                    "suggestedSubmissionName": _submission_name(str(model.name)),
                    "sourceModelName": model.name,
                    "sha256": record.key,
                    "daeFiles": dae_names,
                    "pngTextures": png_names,
                    "hasDaeAnimation": bool(has_animation),
                    "sourceAnimationTypes": sorted({magic for magic, _data in embedded_animations}),
                    "sourceOccurrences": [
                        {key: value for key, value in item.items() if key not in {"textureData", "textureScore"}}
                        for item in record.occurrences
                    ],
                })
            except Exception as exc:
                errors.append({"name": str(model.name), "sha256": record.key, "error": str(exc)})

    report = {
        "target": "The Models Resource",
        "rom": rom_path.name,
        "packaging": "One submission ZIP per unique model; exactly one DAE plus referenced PNG textures",
        "deduplication": "SHA-256 of exact BMD0 model and AB metadata payloads",
        "rawGameFilesIncluded": False,
        "iconsIncludedInZip": False,
        "packages": len(written),
        "packagesWithDaeAnimation": sum(bool(item["hasDaeAnimation"]) for item in written),
        "files": written,
        "errors": errors,
    }
    (output_dir / "submission_catalog.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report
