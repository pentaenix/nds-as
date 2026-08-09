#!/usr/bin/env python3
"""Apply improved Marine Park Empire titles without rerendering submissions."""
from __future__ import annotations

from pathlib import Path
import re
import tempfile
import zipfile

from rae.platforms.windows_iso.bulk_submission import (
    build_submission_jobs,
    submission_category,
)
from rae.platforms.windows_iso.rom import load_descriptor, scan_windows_iso_rom_path
from rae.platforms.windows_iso.submission import submission_title


_REPO_ROOT = Path(__file__).resolve().parents[1]
_ROM = _REPO_ROOT / "roms/Marine Park Empire 2005 PREACTIVATED-ASPM.iso"
_OUTPUT = _REPO_ROOT / "exports/Marine Park Empire"


def _legacy_title(descriptor: dict) -> str:
    model_path = str((descriptor.get("model") or {}).get("path") or "model")
    title = submission_title(model_path)
    return re.sub(r"(?<=[A-Za-z])(?=\d)", " ", title)


def _renamed_archive(source: Path, target: Path, title: str) -> None:
    with zipfile.ZipFile(source) as archive:
        rows = [(info, archive.read(info.filename)) for info in archive.infolist()]
    with tempfile.NamedTemporaryFile(
        prefix="rae_mpe_rename_", suffix=".zip", dir=source.parent, delete=False,
    ) as stream:
        temporary = Path(stream.name)
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for info, payload in rows:
                name = f"{title}.dae" if info.filename.casefold().endswith(".dae") else info.filename
                output = zipfile.ZipInfo(name, date_time=(2000, 1, 1, 0, 0, 0))
                output.compress_type = zipfile.ZIP_DEFLATED
                output.external_attr = 0o100644 << 16
                archive.writestr(output, payload)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    source.unlink()


def main() -> int:
    assets = scan_windows_iso_rom_path(_ROM, cache_root=_REPO_ROOT / ".cache/windows_iso")
    descriptors = [
        descriptor for asset in assets
        if (descriptor := load_descriptor(asset)).get("type") == "model"
    ]
    jobs = build_submission_jobs(descriptors)
    renamed = 0
    for job in jobs:
        if submission_category(job.descriptor) != "animals":
            continue
        old_title = _legacy_title(job.descriptor)
        if old_title == job.title:
            continue
        directory = _OUTPUT / job.category
        old_zip = directory / f"{old_title}.zip"
        new_zip = directory / f"{job.title}.zip"
        if new_zip.exists() or not old_zip.is_file():
            raise RuntimeError(f"cannot safely rename {old_title!r} to {job.title!r}")
        _renamed_archive(old_zip, new_zip, job.title)
        for suffix in ("_icon.png", "_preview.glb", "_preview.png"):
            old = directory / f"{old_title}{suffix}"
            new = directory / f"{job.title}{suffix}"
            if new.exists() or not old.is_file():
                raise RuntimeError(f"matching artifact is unavailable: {old}")
            old.replace(new)
        print(f"{old_title} -> {job.title}")
        renamed += 1
    print(f"Renamed {renamed} submission quartets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
