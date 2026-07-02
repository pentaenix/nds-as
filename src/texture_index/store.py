"""Save/load parsed NSBTX manifest indexes keyed by Nintendo DS game code."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

from ..platforms.nds.nitro.types import PaletteEntry, Tex0Info, TextureEntry
from ..core.assets import Asset
from .paths import is_valid_game_code, normalize_game_code, rom_content_sha256, texture_index_path_for_game_code

Progress = Callable[[str], None]

TEXTURE_INDEX_FORMAT = "rae-texture-index-v1"


@dataclass(slots=True)
class TextureIndexContext:
    game_code: str = ""
    rom_path: str | None = None
    scan_mode: str = "fast"
    cache_root: Path | None = None


def _tex0_to_dict(info: Tex0Info) -> dict:
    return {
        "textures": [
            {"name": tex.name, "teximage_params": tex.teximage_params, "unknown": tex.unknown}
            for tex in info.textures
        ],
        "palettes": [
            {"name": pal.name, "offset": pal.offset, "unknown": pal.unknown}
            for pal in info.palettes
        ],
        "layout_name": info.layout_name,
    }


def _tex0_from_dict(payload: dict) -> Tex0Info:
    return Tex0Info(
        block1=b"",
        block2=b"",
        block3=b"",
        block4=b"",
        textures=[
            TextureEntry(
                name=str(entry["name"]),
                teximage_params=int(entry["teximage_params"]),
                unknown=int(entry.get("unknown", 0)),
            )
            for entry in payload.get("textures", [])
        ],
        palettes=[
            PaletteEntry(
                name=str(entry["name"]),
                offset=int(entry["offset"]),
                unknown=int(entry.get("unknown", 0)),
            )
            for entry in payload.get("palettes", [])
        ],
        layout_name=str(payload.get("layout_name", "")),
    )


def _context_rom_sha256(context: TextureIndexContext) -> str:
    if not context.rom_path:
        return ""
    path = Path(context.rom_path)
    if not path.is_file():
        return ""
    return rom_content_sha256(path)


def _cache_matches(
    payload: dict,
    *,
    context: TextureIndexContext,
    fingerprint: tuple[int, str],
    manifest_cache_version: int,
    rom_sha256: str,
) -> bool:
    if payload.get("format") != TEXTURE_INDEX_FORMAT:
        return False
    if int(payload.get("manifest_cache_version", -1)) != manifest_cache_version:
        return False
    if normalize_game_code(str(payload.get("game_code", ""))) != normalize_game_code(context.game_code):
        return False
    if str(payload.get("scan_mode", "")) != context.scan_mode:
        return False
    if str(payload.get("rom_sha256", "")) != rom_sha256:
        return False
    fp = payload.get("fingerprint") or {}
    if int(fp.get("count", -1)) != fingerprint[0]:
        return False
    if str(fp.get("digest", "")) != fingerprint[1]:
        return False
    return True


def load_texture_index_cache(
    context: TextureIndexContext,
    assets: Iterable[Asset],
    *,
    manifest_cache_version: int,
    progress: Progress | None = None,
    root: Path | None = None,
) -> dict[str, Tex0Info] | None:
    """Load a cached manifest index when the ROM identity and asset fingerprint match."""
    from ..platforms.nds.texture_library import texture_library_fingerprint

    if not is_valid_game_code(context.game_code):
        return None
    path = texture_index_path_for_game_code(context.game_code, root=root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    fingerprint = texture_library_fingerprint(assets)
    rom_sha256 = _context_rom_sha256(context)
    if not _cache_matches(
        payload,
        context=context,
        fingerprint=fingerprint,
        manifest_cache_version=manifest_cache_version,
        rom_sha256=rom_sha256,
    ):
        return None
    manifests: dict[str, Tex0Info] = {}
    for asset_id, manifest_payload in (payload.get("manifests") or {}).items():
        if not isinstance(manifest_payload, dict):
            continue
        manifest = _tex0_from_dict(manifest_payload)
        if manifest.textures:
            manifests[str(asset_id)] = manifest
    if progress:
        code = normalize_game_code(context.game_code)
        progress(
            f"Texture library: loaded cached dictionary index for {code} "
            f"({len(manifests):,} archive(s))."
        )
    return manifests or None


def save_texture_index_cache(
    context: TextureIndexContext,
    assets: Iterable[Asset],
    manifests: dict[str, Tex0Info],
    *,
    manifest_cache_version: int,
    root: Path | None = None,
) -> Path | None:
    """Persist the manifest index for this game code."""
    from ..platforms.nds.texture_library import texture_library_fingerprint

    if not is_valid_game_code(context.game_code):
        return None
    if not manifests:
        return None
    path = texture_index_path_for_game_code(context.game_code, root=root)
    count, digest = texture_library_fingerprint(assets)
    payload = {
        "format": TEXTURE_INDEX_FORMAT,
        "manifest_cache_version": manifest_cache_version,
        "game_code": normalize_game_code(context.game_code),
        "scan_mode": context.scan_mode,
        "rom_sha256": _context_rom_sha256(context),
        "fingerprint": {"count": count, "digest": digest},
        "manifests": {asset_id: _tex0_to_dict(manifest) for asset_id, manifest in sorted(manifests.items())},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        return None
    return path
