"""Readable Pokémon HOME app assets (UI icons, sprites, fonts, audio).

``base.apk`` ships the Unity player data folder (``assets/bin/Data``) unencrypted.
AssetStudioModCLI can load it directly, which exposes all the built-in UI art:
menu icons, buttons, the title-screen Pikachu sheets, fonts, and audio.

This module stages that folder out of the APK once (cached) and exports the
requested asset types with AssetStudio.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

from ...install import project_root
from .assetstudio_preview import _run_assetstudio, assetstudio_available

_APK_DATA_PREFIX = "assets/bin/Data/"

# AssetStudio -t values for the "everything readable" UI export.
UI_ASSET_TYPES = "tex2d,sprite,font,audio,textAsset"


def find_home_apk(mobile_root: Path) -> Path | None:
    for candidate in (mobile_root / "apk" / "base.apk", mobile_root / "base.apk"):
        if candidate.is_file() and zipfile.is_zipfile(candidate):
            return candidate.resolve()
    return None


def stage_apk_unity_data(mobile_root: Path, *, progress=None) -> Path:
    """Extract assets/bin/Data from base.apk into a reusable cache folder."""
    apk = find_home_apk(mobile_root)
    if apk is None:
        raise RuntimeError(f"No base.apk found under {mobile_root}.")

    stage = project_root() / ".cache" / "home-apk-data" / mobile_root.name
    marker = stage / ".rae_staged"
    if marker.is_file():
        return stage

    if progress:
        progress("Staging Unity data folder from base.apk (one-time)…")
    shutil.rmtree(stage, ignore_errors=True)
    with zipfile.ZipFile(apk) as zf:
        for name in zf.namelist():
            if not name.startswith(_APK_DATA_PREFIX) or name.endswith("/"):
                continue
            target = stage / name[len(_APK_DATA_PREFIX):]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
    marker.write_text("ok", encoding="utf-8")
    return stage


def export_home_ui_assets(
    mobile_root: Path,
    output_dir: str | Path,
    *,
    asset_types: str = UI_ASSET_TYPES,
    name_filter: str | None = None,
    progress=None,
) -> list[Path]:
    """Export the readable HOME app UI assets (icons, sprites, fonts, audio)."""
    if not assetstudio_available():
        raise RuntimeError(
            "HOME UI asset export needs AssetStudioModCLI. Download "
            "AssetStudioModCLI_net9_portable and set RAE_ASSETSTUDIO_CLI, or unpack it "
            "to rae/tools/AssetStudioModCLI/."
        )
    stage = stage_apk_unity_data(mobile_root, progress=progress)
    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    if progress:
        progress(f"Exporting HOME app assets ({asset_types}) with AssetStudio…")
    args = [str(stage), "-m", "export", "-t", asset_types, "-g", "type", "-o", str(out_root)]
    if name_filter:
        args.extend(["--filter-by-name", name_filter])
    _run_assetstudio(args)

    produced = sorted(path for path in out_root.rglob("*") if path.is_file())
    if progress:
        progress(f"HOME app asset export complete: {len(produced):,} file(s) in {out_root}")
    return produced
