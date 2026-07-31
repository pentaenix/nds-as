#!/usr/bin/env python3
"""Remove content hashes from surviving Models Resource submission filenames."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any


def _safe_name(value: str) -> str:
    """Match the filename normalization used by the building exporters."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._") or "building"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        json.dump(value, handle, indent=2)
        handle.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _relative_file(export_dir: Path, value: object) -> Path:
    path = export_dir / str(value)
    try:
        path.resolve().relative_to(export_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"catalog path escapes the export directory: {value}") from exc
    return path


def _validate_package(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError(f"ZIP failed CRC validation: {path.name}")
        names = archive.namelist()
        dae = [name for name in names if name.casefold().endswith(".dae")]
        png = [name for name in names if name.casefold().endswith(".png")]
        if any(Path(name).name != name for name in names):
            raise ValueError(f"ZIP is not flat: {path.name}")
        if len(dae) != 1 or len(dae) + len(png) != len(names):
            raise ValueError(f"ZIP must contain one DAE and only PNG companions: {path.name}")


def _current_rows(
    export_dir: Path,
    catalog: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    current: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    known_files: set[Path] = set()
    for raw_row in catalog.get("files") or []:
        row = dict(raw_row)
        package = _relative_file(export_dir, row.get("package"))
        icon_value = row.get("icon")
        if not icon_value:
            icon_value = str(Path(row["package"]).with_name(
                Path(row["package"]).name.removesuffix("_asset.zip") + "_icon.png"
            ))
            row["icon"] = icon_value
        icon = _relative_file(export_dir, icon_value)
        package_exists = package.is_file()
        icon_exists = icon.is_file()
        if package_exists != icon_exists:
            missing = icon if package_exists else package
            raise ValueError(f"submission pair is incomplete; missing {missing.name}")
        if not package_exists:
            removed.append(row)
            continue
        known_files.update((package.resolve(), icon.resolve()))
        current.append(row)

    packages_dir = export_dir / "submission_zips"
    actual_files = {
        path.resolve()
        for pattern in ("*_asset.zip", "*_icon.png")
        for path in packages_dir.glob(pattern)
    }
    unknown = sorted(actual_files - known_files)
    if unknown:
        names = ", ".join(path.name for path in unknown[:5])
        raise ValueError(f"submission files are absent from the catalog: {names}")
    return current, removed


def _rename_plan(
    export_dir: Path,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    targets: dict[str, Path] = {}
    for row in rows:
        old_package = _relative_file(export_dir, row["package"])
        old_icon = _relative_file(export_dir, row["icon"])
        base = _safe_name(str(row["sourceModelName"]))
        new_package = old_package.with_name(f"{base}_asset.zip")
        new_icon = old_icon.with_name(f"{base}_icon.png")
        for target in (new_package, new_icon):
            collision_key = str(target).casefold()
            previous = targets.get(collision_key)
            if previous is not None:
                raise ValueError(f"case-insensitive target collision: {previous.name}, {target.name}")
            targets[collision_key] = target
        for old, new in ((old_package, new_package), (old_icon, new_icon)):
            if new.exists() and new != old:
                raise FileExistsError(f"refusing to overwrite existing file: {new}")
        plan.append({
            "row": row,
            "oldPackage": old_package,
            "newPackage": new_package,
            "oldIcon": old_icon,
            "newIcon": new_icon,
            "packageSha256": _sha256(old_package),
            "iconSha256": _sha256(old_icon),
        })
    return plan


def _updated_overrides(
    path: Path,
    plan: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    data = _read_json(path)
    old_icons = data.get("icons") or {}
    if not isinstance(old_icons, dict):
        raise ValueError("icon_camera_overrides.json has an invalid icons value")
    icons: dict[str, Any] = {}
    for item in plan:
        old_stem = item["oldIcon"].stem
        new_stem = item["newIcon"].stem
        if old_stem in old_icons:
            icons[new_stem] = old_icons[old_stem]
        elif new_stem in old_icons:
            icons[new_stem] = old_icons[new_stem]
    data["icons"] = dict(sorted(icons.items()))
    return data


def _updated_compatibility_report(
    path: Path,
    plan: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    report = _read_json(path)
    package_names = {
        item["oldPackage"].name: item["newPackage"].name for item in plan
    }
    prior_changed = {
        str(item.get("package")): item for item in report.get("changed") or []
    }
    changed: list[dict[str, Any]] = []
    for old_name, new_name in package_names.items():
        if old_name not in prior_changed and new_name not in prior_changed:
            continue
        item = dict(prior_changed.get(old_name) or prior_changed[new_name])
        item["package"] = new_name
        changed.append(item)
    animated = sum(bool(item["row"].get("hasDaeAnimation")) for item in plan)
    report["packagesAudited"] = len(plan)
    report["convertedStaticIdentitySkin"] = len(changed)
    report["preservedAnimated"] = animated
    report["preservedNonTrivialSkin"] = len(plan) - len(changed) - animated
    report["iconsAudited"] = len(plan)
    report["changed"] = changed
    report["errors"] = []
    return report


def clean_submission_names(export_dir: Path) -> dict[str, Any]:
    export_dir = export_dir.resolve()
    catalog_path = export_dir / "submission_catalog.json"
    overrides_path = export_dir / "icon_camera_overrides.json"
    compatibility_path = export_dir / "trimesh_compatibility_report.json"
    report_path = export_dir / "submission_name_cleanup_report.json"

    catalog = _read_json(catalog_path)
    rows, removed = _current_rows(export_dir, catalog)
    plan = _rename_plan(export_dir, rows)
    for item in plan:
        _validate_package(item["oldPackage"])

    updated_catalog = dict(catalog)
    updated_rows: list[dict[str, Any]] = []
    for item in plan:
        row = dict(item["row"])
        row["package"] = str(item["newPackage"].relative_to(export_dir))
        row["icon"] = str(item["newIcon"].relative_to(export_dir))
        updated_rows.append(row)
    updated_catalog["files"] = updated_rows
    updated_catalog["packages"] = len(updated_rows)
    updated_catalog["packagesWithDaeAnimation"] = sum(
        bool(row.get("hasDaeAnimation")) for row in updated_rows
    )
    if isinstance(updated_catalog.get("icons"), dict):
        updated_catalog["icons"]["rendered"] = len(updated_rows)
        updated_catalog["icons"]["errors"] = []

    updated_overrides = _updated_overrides(overrides_path, plan)
    updated_compatibility = _updated_compatibility_report(compatibility_path, plan)
    cleanup_report: dict[str, Any] = {
        "renamedPairs": len(plan),
        "removedCatalogEntries": len(removed),
        "zipAndIconBytesChanged": False,
        "backupPackagesRenamed": False,
        "files": [
            {
                "oldPackage": item["oldPackage"].name,
                "package": item["newPackage"].name,
                "packageSha256": item["packageSha256"],
                "oldIcon": item["oldIcon"].name,
                "icon": item["newIcon"].name,
                "iconSha256": item["iconSha256"],
            }
            for item in plan
        ],
    }

    metadata_paths = [catalog_path, overrides_path, compatibility_path, report_path]
    original_metadata = {
        path: path.read_bytes() if path.is_file() else None for path in metadata_paths
    }
    completed: list[tuple[Path, Path]] = []
    try:
        for item in plan:
            for old_key, new_key in (
                ("oldPackage", "newPackage"),
                ("oldIcon", "newIcon"),
            ):
                old = item[old_key]
                new = item[new_key]
                if old != new:
                    old.rename(new)
                    completed.append((new, old))
        _write_json_atomic(catalog_path, updated_catalog)
        if updated_overrides is not None:
            _write_json_atomic(overrides_path, updated_overrides)
        if updated_compatibility is not None:
            _write_json_atomic(compatibility_path, updated_compatibility)
        _write_json_atomic(report_path, cleanup_report)
    except Exception:
        for new, old in reversed(completed):
            if new.exists() and not old.exists():
                new.rename(old)
        for path, content in original_metadata.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(content)
        raise

    for item in plan:
        if _sha256(item["newPackage"]) != item["packageSha256"]:
            raise RuntimeError(f"package bytes changed: {item['newPackage'].name}")
        if _sha256(item["newIcon"]) != item["iconSha256"]:
            raise RuntimeError(f"icon bytes changed: {item['newIcon'].name}")
        _validate_package(item["newPackage"])
    return cleanup_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path)
    args = parser.parse_args()
    report = clean_submission_names(args.export_dir)
    print(f"Renamed {report['renamedPairs']} ZIP/icon pairs.")
    print(f"Removed {report['removedCatalogEntries']} absent entries from the catalog.")
    print("ZIP payloads and icon pixels were unchanged.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
