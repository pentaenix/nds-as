from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from rae.platforms.windows_iso import container, rom
from rae.platforms.windows_iso.container import InstallShieldEntry, IsoEntry
from rae.platforms.windows_iso.details_module import WindowsIsoDetailsModule
from rae.platforms.windows_iso.profiles import MARINE_PARK_EMPIRE_2005, identify_profile


pytestmark = pytest.mark.windows_iso


def _iso_signature() -> list[IsoEntry]:
    sizes = {"data1.hdr": 7, "data1.cab": 11, "data2.cab": 13}
    return [
        IsoEntry(path=name, size=sizes.get(name, 1))
        for name in MARINE_PARK_EMPIRE_2005.required_iso_members
    ]


def test_parses_7z_listing_and_identifies_marine_park_empire() -> None:
    listing = """
7-Zip
----------
Path = MarineParkEmpire.exe
Folder = -
Size = 86016

Path = mpe.exe
Folder = -
Size = 4468804

Path = data1.hdr
Folder = -
Size = 715098

Path = data1.cab
Folder = -
Size = 18731351

Path = data2.cab
Folder = -
Size = 581132642
"""
    entries = container.parse_7z_slt(listing)
    assert entries[-1] == IsoEntry("data2.cab", 581132642)
    assert identify_profile(entries) is MARINE_PARK_EMPIRE_2005


def test_parses_unshield_catalog_paths_with_spaces_and_ignores_engine_files() -> None:
    listing = r"""
Cabinet: data1.cab
   93933  model\animal\Amur leopard.AM1
    1550  model\animal\Amur leopard.AM3
  1048704  texture\animal\Amur leopard.dds
   258048  <Support>English Files\_IsRes.dll
 --------  -------
          6795 files
"""
    assert container.parse_unshield_listing(listing) == [
        InstallShieldEntry("model/animal/Amur leopard.AM1", 93933),
        InstallShieldEntry("model/animal/Amur leopard.AM3", 1550),
        InstallShieldEntry("texture/animal/Amur leopard.dds", 1048704),
    ]


def test_caches_all_three_installshield_members_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Marine Park Empire.iso"
    source.write_bytes(b"ISO")
    entries = [
        IsoEntry("data1.hdr", 2),
        IsoEntry("data1.cab", 3),
        IsoEntry("data2.cab", 4),
    ]
    calls: list[list[str]] = []

    def fake_run(command) -> str:
        calls.append(list(command))
        output_arg = next(arg for arg in command if arg.startswith("-o"))
        destination = Path(output_arg[2:])
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "data1.hdr").write_bytes(b"hh")
        (destination / "data1.cab").write_bytes(b"cab")
        (destination / "data2.cab").write_bytes(b"data")
        return ""

    monkeypatch.setattr(container, "_run", fake_run)
    cache = container.cache_installshield_cabinets(
        source,
        entries,
        "/mock/7z",
        cache_root=tmp_path / ".cache" / "windows_iso",
    )
    assert cache.is_relative_to(tmp_path / ".cache" / "windows_iso")
    assert calls[0][-3:] == ["data1.hdr", "data1.cab", "data2.cab"]

    again = container.cache_installshield_cabinets(
        source,
        entries,
        "/mock/7z",
        cache_root=tmp_path / ".cache" / "windows_iso",
    )
    assert again == cache
    assert len(calls) == 1


def test_missing_container_tool_returns_setup_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "Marine Park Empire 2005 PREACTIVATED-ASPM.iso"
    source.write_bytes(b"not read")
    monkeypatch.setattr(rom, "find_7z", lambda: None)

    assets = rom.scan_windows_iso_rom_path(source)
    assert len(assets) == 1
    assert assets[0].magic == "WILK"
    descriptor = rom.load_descriptor(assets[0])
    assert descriptor["type"] == "locked"
    assert descriptor["profile_id"] == "marine_park_empire_2005"
    assert "7-Zip" in descriptor["message"]


def test_scan_returns_only_summary_and_model_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "Marine Park Empire.iso"
    source.write_bytes(b"the scanner must not place these raw bytes in an Asset")
    cache = tmp_path / ".cache" / "windows_iso" / "marine-park"
    catalog = [
        InstallShieldEntry("model/item/Train01_Toll.SMO", 47140),
        InstallShieldEntry("model/item/orphan#s.SMO", 2048),
        InstallShieldEntry("model/animal/Orca.AM1", 52493),
        InstallShieldEntry("model/animal/Orca#2.AM1", 32000),
        InstallShieldEntry("model/animal/Orca#3.AM1", 16000),
        InstallShieldEntry("model/animal/Orca#s.AM1", 12000),
        InstallShieldEntry("model/animal/Orca.AM2", 3000),
        InstallShieldEntry("model/animal/Orca.AM3", 1000),
        InstallShieldEntry("anim/Train01_Toll.AM2", 1110),
        InstallShieldEntry("anim/Train01_Toll.AM3", 248),
        InstallShieldEntry("anim/unassociated_wave.AM3", 298),
        InstallShieldEntry("texture/animal/orca.dds", 4096),
        InstallShieldEntry("texture/item/Train01_Toll.tga", 1024),
        InstallShieldEntry("readme.txt", 20),
    ]
    monkeypatch.setattr(rom, "find_7z", lambda: "/mock/7z")
    monkeypatch.setattr(rom, "list_iso_entries", lambda *_args: _iso_signature())
    monkeypatch.setattr(rom, "find_unshield", lambda: "/mock/unshield")
    monkeypatch.setattr(rom, "cache_installshield_cabinets", lambda *_args, **_kwargs: cache)
    monkeypatch.setattr(rom, "list_installshield_entries", lambda *_args: catalog)

    progress: list[str] = []
    assets = rom.scan_windows_iso_rom_path(source, progress=progress.append)

    assert [asset.magic for asset in assets] == ["WISO", "WAM1", "WSMO"]
    assert all(asset.data == asset.original_data for asset in assets)
    assert all(b"the scanner must not place" not in asset.data for asset in assets)

    orca = rom.load_descriptor(assets[1])
    assert orca["model"]["path"] == "model/animal/Orca.AM1"
    assert [row["format"] for row in orca["animations"]] == ["AM2", "AM3"]
    assert [row["path"] for row in orca["textures"]] == ["texture/animal/orca.dds"]
    assert [row["path"] for row in orca["lods"]] == [
        "model/animal/Orca#2.AM1",
        "model/animal/Orca#3.AM1",
    ]
    assert [row["path"] for row in orca["shadows"]] == ["model/animal/Orca#s.AM1"]

    train = rom.load_descriptor(assets[2])
    assert len(train["animations"]) == 2
    assert train["textures"][0]["format"] == "TGA"

    summary = rom.load_descriptor(assets[0])
    assert summary["model_rows"] == 2
    assert summary["format_counts"]["AM3"] == 3
    assert summary["unassociated_animation_records"] == 1
    assert any("2 model rows ready" in line for line in progress)

    details = WindowsIsoDetailsModule().asset_details(assets[1])
    assert details is not None
    assert "Animation source records: 2" in details


def test_unrecognized_iso_returns_information_row(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "unknown.iso"
    source.write_bytes(b"ISO")
    monkeypatch.setattr(rom, "find_7z", lambda: "/mock/7z")
    monkeypatch.setattr(rom, "list_iso_entries", lambda *_args: [IsoEntry("setup.exe", 1)])

    asset = rom.scan_windows_iso_rom_path(source)[0]
    descriptor = json.loads(asset.data)
    assert asset.magic == "WILK"
    assert descriptor["type"] == "information"
    assert "not a recognized" in descriptor["message"]


def test_variant_model_keeps_exact_skeleton_and_base_animation() -> None:
    model = InstallShieldEntry("model/animal/mantaray#2.AM1", 100)
    rows = [
        InstallShieldEntry("model/animal/mantaray#2.AM3", 20),
        InstallShieldEntry("model/animal/mantaray.AM3", 30),
        InstallShieldEntry("anim/mantaray.AM2", 40),
    ]
    related = rom._related_records(model, rom._dependency_index(rows))
    assert {row.path for row in related} == {row.path for row in rows}


def test_platform_modules_import_in_flat_launcher_layout() -> None:
    project = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.modules.registry import get_platform_modules; "
            "assert get_platform_modules('windows_iso').platform_id == 'windows_iso'",
        ],
        cwd=project,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
