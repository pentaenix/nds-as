from __future__ import annotations

import json
from pathlib import Path

import pytest

from rae.ui.recent_roms import MAX_RECENT_ROMS, load_recent_roms, remember_recent_rom


@pytest.fixture
def recent_rom_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    store = tmp_path / ".cache" / "recent_roms.json"
    monkeypatch.setattr("rae.ui.recent_roms.project_root", lambda: tmp_path)
    return store


def test_remember_recent_rom_orders_most_recent_first(recent_rom_store: Path) -> None:
    root = recent_rom_store.parent.parent
    first = root / "one.nds"
    second = root / "two.nds"
    third = root / "three.nds"
    for path in (first, second, third):
        path.write_bytes(b"rom")

    remember_recent_rom(str(first))
    remember_recent_rom(str(second))
    remember_recent_rom(str(third))

    assert load_recent_roms() == [str(third.resolve()), str(second.resolve()), str(first.resolve())]


def test_remember_recent_rom_dedupes_and_caps_list(recent_rom_store: Path) -> None:
    root = recent_rom_store.parent.parent
    paths = []
    for index in range(MAX_RECENT_ROMS + 2):
        path = root / f"game-{index}.nds"
        path.write_bytes(b"rom")
        paths.append(path)
        remember_recent_rom(str(path))

    loaded = load_recent_roms()
    assert len(loaded) == MAX_RECENT_ROMS
    assert loaded[0] == str(paths[-1].resolve())
    assert str(paths[0].resolve()) not in loaded

    stored = json.loads(recent_rom_store.read_text(encoding="utf-8"))
    assert len(stored) == MAX_RECENT_ROMS


def test_load_recent_roms_skips_missing_files(recent_rom_store: Path, tmp_path: Path) -> None:
    present = tmp_path / "present.nds"
    present.write_bytes(b"rom")
    missing = tmp_path / "missing.nds"
    recent_rom_store.parent.mkdir(parents=True, exist_ok=True)
    recent_rom_store.write_text(
        json.dumps([str(missing), str(present)]) + "\n",
        encoding="utf-8",
    )

    assert load_recent_roms() == [str(present.resolve())]
