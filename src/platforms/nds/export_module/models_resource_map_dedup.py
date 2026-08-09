"""Exact-content deduplication for generated Models Resource map packages."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import zipfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class MapPackage:
    title: str
    section: str
    directory: Path
    glb_sha256: str
    icon_sha256: str
    preview_sha256: str
    texture_sha256: tuple[str, ...]


@dataclass(frozen=True)
class DuplicateGroup:
    canonical: MapPackage
    duplicates: tuple[MapPackage, ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _zip_texture_hashes(package: Path) -> tuple[str, ...]:
    """Hash DAE texture payloads independently of title-derived filenames."""
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"ZIP CRC check failed: {package}")
        return tuple(sorted(
            hashlib.sha256(archive.read(name)).hexdigest()
            for name in archive.namelist()
            if name.casefold().endswith(".png")
        ))


def discover_complete_packages(output_root: Path) -> list[MapPackage]:
    packages: list[MapPackage] = []
    for section in ("Maps", "Interior Maps"):
        locations = output_root / section / "Locations"
        if not locations.is_dir():
            continue
        for directory in sorted(path for path in locations.iterdir() if path.is_dir()):
            title = directory.name
            archive = directory / f"{title}.zip"
            icon = directory / f"{title}_icon.png"
            preview = directory / f"{title}_preview.png"
            glb = directory / f"{title}_preview.glb"
            if not all(path.is_file() for path in (archive, icon, preview, glb)):
                continue
            packages.append(MapPackage(
                title=title,
                section=section,
                directory=directory,
                glb_sha256=_sha256(glb),
                icon_sha256=_sha256(icon),
                preview_sha256=_sha256(preview),
                texture_sha256=_zip_texture_hashes(archive),
            ))
    return packages


def _identity(package: MapPackage) -> tuple[str, str, str, tuple[str, ...]]:
    # The GLB is the deterministic source used to produce the DAE. Assimp adds
    # timestamps and nondeterministic near-zero controller values, so comparing
    # raw DAE bytes would fail for two conversions of the exact same GLB.
    return (
        package.glb_sha256,
        package.icon_sha256,
        package.preview_sha256,
        package.texture_sha256,
    )


def _canonical_key(package: MapPackage) -> tuple[int, int, str]:
    area_suffix = re.search(r"\s+Area\s+\d+$", package.title, re.IGNORECASE)
    return (1 if area_suffix else 0, len(package.title), package.title.casefold())


def find_exact_duplicate_groups(packages: list[MapPackage]) -> list[DuplicateGroup]:
    grouped: dict[tuple[str, str, str, tuple[str, ...]], list[MapPackage]] = defaultdict(list)
    for package in packages:
        grouped[_identity(package)].append(package)
    result: list[DuplicateGroup] = []
    for matches in grouped.values():
        if len(matches) < 2:
            continue
        ordered = sorted(matches, key=_canonical_key)
        result.append(DuplicateGroup(ordered[0], tuple(ordered[1:])))
    return sorted(result, key=lambda group: (group.canonical.section, group.canonical.title.casefold()))


def _disable_review_rows(review_path: Path, groups: list[DuplicateGroup]) -> list[str]:
    if not review_path.is_file():
        return []
    duplicate_titles = {
        (group.canonical.section, duplicate.title): group.canonical.title
        for group in groups
        for duplicate in group.duplicates
    }
    with review_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        rows = list(reader)
    changed: list[str] = []
    for row in rows:
        canonical = duplicate_titles.get((row.get("section", ""), row.get("title", "")))
        if canonical is None:
            continue
        row["include"] = "no"
        note = row.get("notes", "").strip()
        marker = f"Exact exported duplicate of {canonical}; disabled by map deduplicator."
        row["notes"] = f"{note} {marker}".strip() if marker not in note else note
        changed.append(row.get("key", ""))
    temporary = review_path.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(review_path)
    return changed


def apply_exact_deduplication(
    output_root: Path,
    groups: list[DuplicateGroup],
    *,
    quarantine_root: Path | None = None,
) -> tuple[Path, list[dict[str, str]], list[str]]:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    quarantine = quarantine_root or output_root.parent / f"{output_root.name}_duplicate_quarantine_{timestamp}"
    moved: list[dict[str, str]] = []
    for group in groups:
        for duplicate in group.duplicates:
            relative = duplicate.directory.relative_to(output_root)
            target = quarantine / relative
            if target.exists():
                raise FileExistsError(f"Quarantine target already exists: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(duplicate.directory), str(target))
            moved.append({
                "canonical": group.canonical.title,
                "duplicate": duplicate.title,
                "from": str(duplicate.directory),
                "to": str(target),
                "glb_sha256": duplicate.glb_sha256,
            })
    review_keys = _disable_review_rows(output_root / "map_review.csv", groups)
    report = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "output_root": str(output_root),
        "quarantine_root": str(quarantine),
        "duplicate_groups": len(groups),
        "removed_packages": len(moved),
        "disabled_review_keys": review_keys,
        "moves": moved,
    }
    report_path = output_root / "map_duplicate_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return quarantine, moved, review_keys


def report_rows(groups: list[DuplicateGroup]) -> list[dict[str, object]]:
    return [
        {
            "canonical": group.canonical.title,
            "section": group.canonical.section,
            "duplicates": [duplicate.title for duplicate in group.duplicates],
            "glb_sha256": group.canonical.glb_sha256,
        }
        for group in groups
    ]
