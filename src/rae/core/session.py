
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..scanner import Asset

Progress = Callable[[str], None]

SESSION_FORMAT = "rae-session-v1"
LEGACY_SESSION_FORMAT = "dsm-session-v1"


def _safe_name(asset: Asset, suffix: str) -> str:
    return f"assets/{asset.asset_id}{suffix}"


def save_session_zip(
    path: str | Path,
    *,
    assets: list[Asset],
    rom_path: str | None,
    rom_game_code: str = "",
    rom_title: str = "",
    profile_text: str = "",
    mapping_id: str = "",
    pinned_texture_asset_id: str | None = None,
    texture_assignments: dict[str, dict[str, str]] | None = None,
    texture_sequences: dict[str, dict] | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write a self-contained RAE session.

    The session contains the detected asset payloads. It intentionally does not
    store or require the original ROM after it is created. Sessions may contain
    copyrighted extracted data, so the repo .gitignore must keep saves/ out of
    version control.
    """
    target = Path(path)
    if target.suffix.lower() not in {".raesession", ".dsmsession", ".zip"}:
        target = target.with_suffix(".raesession")
    target.parent.mkdir(parents=True, exist_ok=True)

    manifest_assets = []
    total = len(assets)
    if progress:
        progress(f"Saving RAE session with {total} asset(s): {target}")
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for idx, asset in enumerate(assets, start=1):
            if progress and (idx == 1 or idx % 250 == 0 or idx == total):
                progress(f"Session save {idx}/{total}: {asset.virtual_path}")
            data_name = _safe_name(asset, ".bin")
            zf.writestr(data_name, asset.data)
            original_name = None
            if asset.original_data != asset.data:
                original_name = _safe_name(asset, ".original.bin")
                zf.writestr(original_name, asset.original_data)
            manifest_assets.append({
                "asset_id": asset.asset_id,
                "virtual_path": asset.virtual_path,
                "kind": asset.kind,
                "magic": asset.magic,
                "extension": asset.extension,
                "data_file": data_name,
                "original_file": original_name,
                "rom_file_id": asset.rom_file_id,
                "rom_offset": asset.rom_offset,
                "compressed": asset.compressed,
                "container_chain": list(asset.container_chain),
                "carved": asset.carved,
                "carved_offset": asset.carved_offset,
                "mapping_category": asset.mapping_category,
                "mapping_label": asset.mapping_label,
                "mapping_confidence": asset.mapping_confidence,
            })
        manifest = {
            "format": SESSION_FORMAT,
            "created_utc": datetime.now(timezone.utc).isoformat(),
            "source_rom_name": Path(rom_path).name if rom_path else "",
            "source_rom_path_note": str(rom_path or ""),
            "source_rom_game_code": rom_game_code,
            "source_rom_title": rom_title,
            "asset_count": total,
            "profile_text": profile_text,
            "mapping_id": mapping_id,
            "pinned_texture_asset_id": pinned_texture_asset_id,
            "texture_assignments": dict(texture_assignments or {}),
            "texture_sequences": dict(texture_sequences or {}),
            "assets": manifest_assets,
        }
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
    if progress:
        progress(f"Session saved: {target}")
    return target


def load_session_zip(path: str | Path, *, progress: Progress | None = None) -> dict:
    source = Path(path)
    if progress:
        progress(f"Opening RAE session: {source}")
    with zipfile.ZipFile(source, "r") as zf:
        manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
        fmt = manifest.get("format")
        if fmt not in {SESSION_FORMAT, LEGACY_SESSION_FORMAT}:
            raise ValueError("This file is not a supported RAE session.")
        assets: list[Asset] = []
        raw_assets = list(manifest.get("assets", []))
        total = len(raw_assets)
        for idx, raw in enumerate(raw_assets, start=1):
            if progress and (idx == 1 or idx % 250 == 0 or idx == total):
                progress(f"Session load {idx}/{total}: {raw.get('virtual_path', '')}")
            data = zf.read(raw["data_file"])
            original_file = raw.get("original_file")
            original = zf.read(original_file) if original_file else data
            assets.append(Asset(
                asset_id=str(raw.get("asset_id", f"asset_{idx}")),
                virtual_path=str(raw.get("virtual_path", "")),
                kind=str(raw.get("kind", "")),
                magic=str(raw.get("magic", "")),
                extension=str(raw.get("extension", "")),
                data=data,
                original_data=original,
                rom_file_id=raw.get("rom_file_id"),
                rom_offset=raw.get("rom_offset"),
                compressed=bool(raw.get("compressed", False)),
                container_chain=tuple(str(x) for x in raw.get("container_chain", [])),
                carved=bool(raw.get("carved", False)),
                carved_offset=raw.get("carved_offset"),
                mapping_category=str(raw.get("mapping_category", "unknown")),
                mapping_label=str(raw.get("mapping_label", "")),
                mapping_confidence=str(raw.get("mapping_confidence", "")),
            ))
    if progress:
        progress(f"Session loaded: {len(assets)} asset(s).")
    return {"manifest": manifest, "assets": assets, "path": source}
