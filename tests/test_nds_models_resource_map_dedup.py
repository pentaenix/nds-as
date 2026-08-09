from __future__ import annotations

import csv
import zipfile
from pathlib import Path

import pytest

from rae.platforms.nds.export_module.models_resource_map_dedup import (
    apply_exact_deduplication,
    discover_complete_packages,
    find_exact_duplicate_groups,
)

pytestmark = pytest.mark.nds


def _package(root: Path, title: str, *, glb: bytes = b"same-glb") -> Path:
    directory = root / "Maps" / "Locations" / title
    directory.mkdir(parents=True)
    (directory / f"{title}_preview.glb").write_bytes(glb)
    (directory / f"{title}_icon.png").write_bytes(b"same-icon")
    (directory / f"{title}_preview.png").write_bytes(b"same-preview")
    with zipfile.ZipFile(directory / f"{title}.zip", "w") as archive:
        archive.writestr(f"{title}.dae", f"<COLLADA><asset>{title}</asset></COLLADA>")
        archive.writestr(f"{title}_texture_0001.png", b"same-texture")
    return directory


def _review(root: Path) -> None:
    with (root / "map_review.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=("include", "key", "section", "title", "notes"))
        writer.writeheader()
        writer.writerow({"include": "yes", "key": "base", "section": "Maps", "title": "Route 12", "notes": ""})
        writer.writerow({"include": "yes", "key": "alias", "section": "Maps", "title": "Route 12 Area 2", "notes": ""})


def test_exact_deduplication_prefers_base_title_and_quarantines_alias(tmp_path: Path) -> None:
    output = tmp_path / "pokemon_black_2"
    canonical = _package(output, "Route 12")
    duplicate = _package(output, "Route 12 Area 2")
    _review(output)

    groups = find_exact_duplicate_groups(discover_complete_packages(output))

    assert len(groups) == 1
    assert groups[0].canonical.title == "Route 12"
    quarantine, moved, review_keys = apply_exact_deduplication(output, groups)
    assert canonical.is_dir()
    assert not duplicate.exists()
    assert (quarantine / "Maps" / "Locations" / "Route 12 Area 2").is_dir()
    assert len(moved) == 1
    assert review_keys == ["alias"]
    with (output / "map_review.csv").open(encoding="utf-8", newline="") as handle:
        rows = {row["key"]: row for row in csv.DictReader(handle)}
    assert rows["base"]["include"] == "yes"
    assert rows["alias"]["include"] == "no"


def test_different_glb_payloads_are_not_deduplicated(tmp_path: Path) -> None:
    output = tmp_path / "pokemon_black_2"
    _package(output, "Route 12", glb=b"first")
    _package(output, "Route 12 Area 2", glb=b"second")

    assert find_exact_duplicate_groups(discover_complete_packages(output)) == []
