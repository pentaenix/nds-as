#!/usr/bin/env python3
"""Make trivial static DAE skins broadly readable without touching icons."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from flatten_static_collada_skin import flatten_static_skin


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_flat_zip(output: Path, files: list[Path]) -> None:
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for path in sorted(files, key=lambda item: item.name.casefold()):
                info = zipfile.ZipInfo(path.name, date_time=(2000, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, path.read_bytes())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("rewritten ZIP failed CRC validation")
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def _is_already_direct(dae: Path) -> bool:
    root = ET.parse(dae).getroot()
    tags = [node.tag.rsplit("}", 1)[-1] for node in root.iter()]
    return "instance_geometry" in tags and "instance_controller" not in tags


def convert_packages(packages_dir: Path, backup_dir: Path) -> dict[str, object]:
    packages = sorted(packages_dir.glob("*_asset.zip"))
    icon_hashes = {
        path.name: _sha256(path)
        for path in sorted(packages_dir.glob("*_icon.png"))
    }
    backup_dir.mkdir(parents=True, exist_ok=True)
    counts: Counter[str] = Counter()
    changed: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    with tempfile.TemporaryDirectory(prefix="rae-static-dae-packages-") as name:
        work_root = Path(name)
        for index, package in enumerate(packages, 1):
            print(f"Checking package {index}/{len(packages)}: {package.name}", flush=True)
            work_dir = work_root / f"package-{index:04d}"
            work_dir.mkdir()
            try:
                with zipfile.ZipFile(package) as archive:
                    names = archive.namelist()
                    if any(Path(item).name != item for item in names):
                        raise ValueError("package contains a non-flat path")
                    archive.extractall(work_dir)
                dae_files = list(work_dir.glob("*.dae"))
                png_files = list(work_dir.glob("*.png"))
                if len(dae_files) != 1 or len(dae_files) + len(png_files) != len(names):
                    raise ValueError("expected exactly one DAE and only PNG companions")
                dae = dae_files[0]
                if _is_already_direct(dae):
                    counts["alreadyDirect"] += 1
                    continue
                try:
                    result = flatten_static_skin(dae, dae)
                except ValueError as exc:
                    reason = str(exc)
                    if reason == "refusing to flatten a DAE containing animation data":
                        counts["preservedAnimated"] += 1
                    elif reason == "skin is not a single identity-weight joint":
                        counts["preservedNonTrivialSkin"] += 1
                    else:
                        raise
                    continue

                before = _sha256(package)
                backup = backup_dir / package.name
                if backup.exists():
                    if _sha256(backup) != before:
                        raise ValueError(f"backup differs from current source: {backup}")
                else:
                    shutil.copy2(package, backup)
                _write_flat_zip(package, dae_files + png_files)
                after = _sha256(package)
                counts["convertedStaticIdentitySkin"] += 1
                changed.append({
                    "package": package.name,
                    "beforeSha256": before,
                    "afterSha256": after,
                    "geometry": str(result["geometry"]),
                    "vertices": str(result["vertices"]),
                })
            except Exception as exc:
                counts["errors"] += 1
                errors.append({"package": package.name, "error": str(exc)})

    current_icon_hashes = {
        path.name: _sha256(path)
        for path in sorted(packages_dir.glob("*_icon.png"))
    }
    icons_unchanged = current_icon_hashes == icon_hashes
    if not icons_unchanged:
        raise RuntimeError("one or more icon files changed during DAE conversion")
    report: dict[str, object] = {
        "packagesAudited": len(packages),
        **dict(counts),
        "iconsAudited": len(icon_hashes),
        "iconsUnchanged": icons_unchanged,
        "backupDirectory": str(backup_dir),
        "changed": changed,
        "errors": errors,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("packages_dir", type=Path)
    parser.add_argument("--backup-dir", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    packages_dir = args.packages_dir.resolve()
    backup_dir = (
        args.backup_dir.resolve()
        if args.backup_dir
        else packages_dir.parent / "original_skin_controller_zips"
    )
    report_path = (
        args.report.resolve()
        if args.report
        else packages_dir.parent / "trimesh_compatibility_report.json"
    )
    report = convert_packages(packages_dir, backup_dir)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "changed"}, indent=2))
    print(f"Report: {report_path}")
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
