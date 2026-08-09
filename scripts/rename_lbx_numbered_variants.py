#!/usr/bin/env python3
"""Rename completed LBX numbered variants to letter-before-number titles."""
from __future__ import annotations

import argparse
from copy import copy
from pathlib import Path, PurePosixPath
import re
import zipfile

from rae.install import project_root
from rae.platforms.threeds.lbx_catalog import lbx_variant_letter


_OLD_TITLE = re.compile(
    r"^(?P<label>.+?) (?P<number>\d{2}) (?P<variant>\d+)(?P<size> [SML])?$"
)
_NEW_TITLE = re.compile(
    r"^(?P<label>.+?) (?P<variant>[A-Z]+) (?P<number>\d{2})(?P<size> [SML])?$"
)
_COMPANIONS = (".zip", "_icon.png", "_preview.png", "_preview.glb")


def renamed_title(title: str) -> str | None:
    match = _OLD_TITLE.fullmatch(title)
    if match is None:
        return None
    letter = lbx_variant_letter(int(match.group("variant")) - 1)
    return (
        f"{match.group('label')} {letter} {match.group('number')}"
        f"{match.group('size') or ''}"
    )


def _variant_number(letters: str) -> int:
    value = 0
    for letter in letters:
        value = value * 26 + ord(letter) - ord("A") + 1
    return value


def original_title(title: str) -> str | None:
    match = _NEW_TITLE.fullmatch(title)
    if match is None:
        return None
    return (
        f"{match.group('label')} {match.group('number')} "
        f"{_variant_number(match.group('variant'))}{match.group('size') or ''}"
    )


def discover(root: Path) -> list[tuple[Path, str]]:
    changes: list[tuple[Path, str]] = []
    for category in ("Chips", "Weapons"):
        category_root = root / category
        if not category_root.is_dir():
            continue
        for subcategory in sorted(path for path in category_root.iterdir() if path.is_dir()):
            for directory in sorted(path for path in subcategory.iterdir() if path.is_dir()):
                replacement = renamed_title(directory.name)
                if replacement is not None:
                    changes.append((directory, replacement))
    return changes


def discover_renamed(root: Path) -> list[tuple[Path, str]]:
    changes: list[tuple[Path, str]] = []
    for category in ("Chips", "Weapons"):
        category_root = root / category
        if not category_root.is_dir():
            continue
        for subcategory in sorted(path for path in category_root.iterdir() if path.is_dir()):
            for directory in sorted(path for path in subcategory.iterdir() if path.is_dir()):
                previous = original_title(directory.name)
                if previous is not None:
                    changes.append((directory, previous))
    return changes


def _renamed_member(name: str, old_title: str, new_title: str) -> str:
    path = PurePosixPath(name)
    if path.stem != old_title:
        return name
    return str(path.with_name(new_title + path.suffix))


def _rewrite_archive(path: Path, old_title: str, new_title: str) -> None:
    temporary = path.with_suffix(".zip.renaming")
    with zipfile.ZipFile(path, "r") as source, zipfile.ZipFile(temporary, "w") as target:
        target.comment = source.comment
        for info in source.infolist():
            renamed = copy(info)
            renamed.filename = _renamed_member(info.filename, old_title, new_title)
            target.writestr(renamed, source.read(info.filename))
    with zipfile.ZipFile(temporary, "r") as check:
        corrupt = check.testzip()
        if corrupt is not None:
            temporary.unlink(missing_ok=True)
            raise RuntimeError(f"rewritten archive is corrupt at {corrupt}: {path}")
    temporary.replace(path)


def apply_change(directory: Path, new_title: str) -> None:
    old_title = directory.name
    target = directory.with_name(new_title)
    if target.exists():
        raise FileExistsError(f"rename target already exists: {target}")
    existing = {
        suffix: directory / f"{old_title}{suffix}"
        for suffix in _COMPANIONS
        if (directory / f"{old_title}{suffix}").is_file()
    }
    archive = existing.get(".zip")
    if archive is not None:
        _rewrite_archive(archive, old_title, new_title)
    renamed_files: list[tuple[Path, Path]] = []
    try:
        for suffix, source in existing.items():
            destination = directory / f"{new_title}{suffix}"
            source.rename(destination)
            renamed_files.append((source, destination))
        directory.rename(target)
    except Exception:
        for source, destination in reversed(renamed_files):
            if destination.exists() and not source.exists():
                destination.rename(source)
        raise


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=project_root() / "exports/LBX"
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--rebuild-report",
        action="store_true",
        help="reconstruct the before/after report after a completed migration",
    )
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    root = args.root.expanduser().resolve()
    changes = discover(root)
    if args.rebuild_report:
        completed = discover_renamed(root)
        lines = [
            f"{directory.relative_to(root).with_name(old_title)} -> "
            f"{directory.relative_to(root)}"
            for directory, old_title in completed
        ]
    else:
        lines = [
            f"{directory.relative_to(root)} -> "
            f"{directory.relative_to(root).with_name(new_title)}"
            for directory, new_title in changes
        ]
    if args.apply:
        for directory, new_title in changes:
            apply_change(directory, new_title)
    report = args.report or root / "numbered_variant_renames.txt"
    if lines or args.rebuild_report or not report.exists():
        report.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    action = "Renamed" if args.apply else "Planned"
    count = len(lines) if args.rebuild_report else len(changes)
    print(f"{action} {count:,} LBX numbered variants.")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
