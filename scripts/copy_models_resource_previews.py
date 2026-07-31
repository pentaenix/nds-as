#!/usr/bin/env python3
"""Copy catalog-matched GLBs beside Models Resource submission files."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import struct
import tempfile
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_glb(path: Path) -> None:
    size = path.stat().st_size
    if size < 20:
        raise ValueError(f"GLB is truncated: {path.name}")
    with path.open("rb") as handle:
        magic, version, declared_size = struct.unpack("<4sII", handle.read(12))
        json_size, json_kind = struct.unpack("<I4s", handle.read(8))
        json_bytes = handle.read(json_size)
    if magic != b"glTF" or version != 2 or declared_size != size:
        raise ValueError(f"invalid GLB header: {path.name}")
    if json_kind != b"JSON":
        raise ValueError(f"GLB has no leading JSON chunk: {path.name}")
    document = json.loads(json_bytes.rstrip(b" \t\r\n\0"))
    for image in document.get("images") or []:
        if "uri" in image:
            raise ValueError(f"GLB contains an external image reference: {path.name}")


def copy_previews(dae_export_dir: Path, glb_export_dir: Path) -> dict[str, Any]:
    dae_export_dir = dae_export_dir.resolve()
    glb_export_dir = glb_export_dir.resolve()
    catalog_path = dae_export_dir / "submission_catalog.json"
    report_path = dae_export_dir / "preview_copy_report.json"
    catalog = _read_json(catalog_path)
    glb_catalog = _read_json(glb_export_dir / "building_catalog.json")

    glb_by_hash: dict[str, Path] = {}
    for row in glb_catalog.get("files") or []:
        key = str(row["sha256"])
        source = glb_export_dir / str(row["file"])
        if key in glb_by_hash:
            raise ValueError(f"duplicate GLB catalog hash: {key}")
        glb_by_hash[key] = source

    rows = list(catalog.get("files") or [])
    planned: list[dict[str, Any]] = []
    target_names: set[str] = set()
    for row in rows:
        package = dae_export_dir / str(row["package"])
        suffix = "_asset.zip"
        if not package.name.endswith(suffix) or not package.is_file():
            raise ValueError(f"missing or invalid submission package: {package}")
        base = package.name.removesuffix(suffix)
        target = package.with_name(f"{base}_preview.glb")
        target_key = target.name.casefold()
        if target_key in target_names:
            raise ValueError(f"preview filename collision: {target.name}")
        target_names.add(target_key)
        source = glb_by_hash.get(str(row["sha256"]))
        if source is None or not source.is_file():
            raise FileNotFoundError(f"matching GLB is unavailable for {package.name}")
        _validate_glb(source)
        planned.append({"row": row, "source": source, "target": target})

    packages_dir = dae_export_dir / "submission_zips"
    expected = {item["target"].resolve() for item in planned}
    unexpected = {
        path.resolve() for path in packages_dir.glob("*_preview.glb")
    } - expected
    if unexpected:
        names = ", ".join(sorted(path.name for path in unexpected)[:5])
        raise ValueError(f"unexpected existing preview GLBs: {names}")

    copied: list[Path] = []
    report_rows: list[dict[str, str]] = []
    try:
        for item in planned:
            source = item["source"]
            target = item["target"]
            source_hash = _sha256(source)
            if target.exists():
                if _sha256(target) != source_hash:
                    raise FileExistsError(f"refusing to overwrite different preview: {target}")
            else:
                shutil.copy2(source, target)
                copied.append(target)
            if _sha256(target) != source_hash:
                raise RuntimeError(f"copied preview differs from source: {target.name}")
            _validate_glb(target)
            item["row"]["preview"] = str(target.relative_to(dae_export_dir))
            report_rows.append({
                "package": Path(str(item["row"]["package"])).name,
                "source": source.name,
                "preview": target.name,
                "sha256": source_hash,
            })

        catalog["previewModels"] = {
            "format": "GLB",
            "copied": len(report_rows),
            "embeddedTexturesRequired": True,
            "insideSubmissionZip": False,
        }
        report = {
            "previewsCopied": len(report_rows),
            "sourcesMoved": False,
            "namesMatchSubmissionPackages": True,
            "files": report_rows,
        }
        _write_json_atomic(catalog_path, catalog)
        _write_json_atomic(report_path, report)
        return report
    except Exception:
        for path in reversed(copied):
            path.unlink(missing_ok=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dae_export_dir", type=Path)
    parser.add_argument("glb_export_dir", type=Path)
    args = parser.parse_args()
    report = copy_previews(args.dae_export_dir, args.glb_export_dir)
    print(f"Copied {report['previewsCopied']} matching GLB previews.")
    print("Original GLBs were left in place.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
