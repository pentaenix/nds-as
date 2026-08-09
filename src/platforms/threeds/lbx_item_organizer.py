"""Reorganize already-exported LBX item submissions after human review."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import tempfile
import zipfile

from ...install import project_root
from .lbx_item_review import item_export_job, review_state_path, save_review_state


def _rename_archive(archive: Path, old_title: str, new_title: str) -> None:
    if old_title == new_title:
        return
    with tempfile.TemporaryDirectory(prefix="rae-lbx-item-") as temporary:
        root = Path(temporary)
        with zipfile.ZipFile(archive) as source:
            source.extractall(root)
        old_dae = root / f"{old_title}.dae"
        if old_dae.is_file():
            old_dae.rename(root / f"{new_title}.dae")
        replacement = archive.with_suffix(".tmp")
        with zipfile.ZipFile(replacement, "w", zipfile.ZIP_DEFLATED) as output:
            for file in sorted(root.rglob("*")):
                if file.is_file():
                    output.write(file, file.relative_to(root).as_posix())
        replacement.replace(archive)


def organize_reviewed_item_packages(root: Path | None = None) -> dict[str, int]:
    """Move selected packages to their canonical folders and apply safe renames."""
    export_root = Path(root) if root else project_root() / "exports"
    state_path = review_state_path()
    state = json.loads(state_path.read_text(encoding="utf-8"))
    selected = {
        path: decision
        for path, decision in state.get("decisions", {}).items()
        if decision.get("status") == "selected" and decision.get("exported")
    }
    moved = renamed = missing = 0
    for romfs_path, decision in selected.items():
        old_title = str(decision.get("title") or "").strip()
        job = item_export_job(romfs_path, old_title)
        matches = [
            package
            for package in (export_root / "LBX").glob(f"**/{old_title}")
            if (package / f"{old_title}.zip").is_file()
        ]
        if not matches:
            missing += 1
            continue
        source = matches[0]
        destination = export_root / job.relative_directory
        if source != destination:
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                raise FileExistsError(destination)
            shutil.move(str(source), str(destination))
            moved += 1
        if old_title != job.title:
            archive = destination / f"{old_title}.zip"
            _rename_archive(archive, old_title, job.title)
            for suffix in (".zip", "_icon.png", "_preview.png", "_preview.glb"):
                old_file = destination / f"{old_title}{suffix}"
                if old_file.exists():
                    old_file.rename(destination / f"{job.title}{suffix}")
            renamed += 1
        decision["title"] = job.title
    save_review_state(state)
    for directory in sorted((export_root / "LBX" / "Items").glob("**/*"), reverse=True):
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    return {"moved": moved, "renamed": renamed, "missing": missing}
