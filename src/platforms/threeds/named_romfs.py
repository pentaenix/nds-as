"""Generic named-RomFS asset catalog for Nintendo 3DS games.

Unlike Game Freak's anonymous GARC layout, many 3DS titles expose NintendoWare
files by name.  This scanner is intentionally game-neutral: game profiles may
add relationships later, while every compatible ROM immediately gets useful
model, texture, and animation rows.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from typing import Callable, Iterable

from ...core.assets import Asset
from .container import RomFsFile, ThreedsImage


_NAMED_FORMATS: dict[str, tuple[str, str, str, str]] = {
    ".bcmdl": ("cgfx_model", "CGMD", "3DS CGFX model", "models"),
    ".bcres": ("cgfx_model", "CGMD", "3DS CGFX resource", "models"),
    ".cgfx": ("cgfx_model", "CGMD", "3DS CGFX resource", "models"),
    ".bctex": ("cgfx_texture", "CGTX", "3DS CGFX texture", "textures"),
    ".bcskla": ("cgfx_skeletal_animation", "CGSA", "3DS skeletal animation", "animations/skeletal"),
    ".bcmata": ("cgfx_material_animation", "CGMA", "3DS material animation", "animations/material"),
    ".bccam": ("cgfx_camera_animation", "CGCA", "3DS camera animation", "animations/camera"),
}

CGFX_MAGICS = frozenset({"CGMD", "CGTX", "CGSA", "CGMA", "CGCA"})


def _format_for(path: str) -> tuple[str, str, str, str] | None:
    lower = path.lower()
    for suffix, info in _NAMED_FORMATS.items():
        if lower.endswith(suffix):
            return info
    return None


def has_named_threeds_assets(files: Iterable[RomFsFile]) -> bool:
    return any(_format_for(entry.path) is not None for entry in files)


def scan_named_romfs_assets(
    image: ThreedsImage,
    files: Iterable[RomFsFile],
    *,
    rom_path: str,
    rom_stem: str,
    product_code: str,
    game_id: str,
    report: Callable[[str], None],
) -> list[Asset]:
    assets: list[Asset] = []
    totals: dict[str, int] = {}
    for entry in sorted(files, key=lambda item: item.path.casefold()):
        info = _format_for(entry.path)
        if info is None:
            continue
        descriptor_type, magic, kind, group = info
        # File extensions are useful hints, but a CGFX header check prevents
        # unrelated files with a recycled suffix from entering model routing.
        if image.read(entry.offset, 4) != b"CGFX":
            continue
        source_name = PurePosixPath(entry.path).name
        stem = PurePosixPath(source_name).stem
        digest = hashlib.sha1(entry.path.encode("utf-8")).hexdigest()[:10]
        payload = {
            "type": descriptor_type,
            "game": game_id,
            "product_code": product_code,
            "rom": rom_path,
            "romfs_path": entry.path,
            "offset": entry.offset,
            "size": entry.size,
            "name": stem,
        }
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        relative = entry.path.lstrip("/")
        assets.append(
            Asset(
                asset_id=f"threeds_{magic.lower()}_{digest}",
                virtual_path=f"3ds/{rom_stem}/{group}/{relative}",
                kind=kind,
                magic=magic,
                extension=PurePosixPath(source_name).suffix.lower(),
                data=data,
                original_data=data,
                rom_offset=entry.offset,
                mapping_category=f"3ds/{game_id}/{group}",
                mapping_label=f"{stem} — {entry.path}",
                mapping_confidence="romfs-name+cgfx-magic",
            )
        )
        totals[magic] = totals.get(magic, 0) + 1
    report(f"3DS: named RomFS scan — {len(assets)} rows {totals}")
    return assets


def read_named_romfs_payload(descriptor: dict) -> bytes:
    """Read one descriptor-backed RomFS file lazily from its CCI/CXI."""
    rom = descriptor.get("rom")
    path = str(descriptor.get("romfs_path") or "")
    if not rom or not path:
        raise ValueError("named 3DS descriptor is missing its ROM or RomFS path")
    with ThreedsImage(rom) as image:
        entry = next((item for item in image.romfs_files() if item.path == path), None)
        if entry is None:
            raise FileNotFoundError(f"{path} not present in {rom}")
        return image.read(entry.offset, entry.size)
