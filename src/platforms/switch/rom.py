"""Nintendo Switch ROM scan entry point.

Opens an NSP/XCI, decrypts the Program NCA's RomFS (given the user's
``prod.keys``) and emits descriptor rows for the Trinity assets — models,
textures, animations, icons. Heavy decode happens lazily in ``service.py`` when
a row is previewed or exported, so scanning stays fast.

When keys are missing the scan still succeeds, returning a single informational
row that explains how to unlock the ROM instead of raising.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from ...core.assets import Asset
from .keys import SwitchKeyError, SwitchKeys, find_prod_keys


def _descriptor_asset(
    *,
    asset_id: str,
    virtual_path: str,
    kind: str,
    magic: str,
    payload: dict,
    mapping_label: str,
) -> Asset:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return Asset(
        asset_id=asset_id,
        virtual_path=virtual_path,
        kind=kind,
        magic=magic,
        extension=".json",
        data=data,
        original_data=data,
        mapping_category="switch",
        mapping_label=mapping_label,
        mapping_confidence="switch-romfs",
    )


def _locked_row(source: Path, message: str) -> Asset:
    return _descriptor_asset(
        asset_id="switch_locked",
        virtual_path=f"switch/{source.stem}/rae_switch_locked.json",
        kind="Nintendo Switch (locked)",
        magic="SWLK",
        payload={"type": "locked", "rom": str(source), "message": message},
        mapping_label=f"{source.stem} — keys required",
    )


def _scan_trinity_assets(
    source: Path,
    report: Callable[[str], None],
) -> list[Asset]:
    from .hash_cache import HashCache
    from .service import _cache_path, iter_named_trinity_files, open_archive

    cache_path = _cache_path(source)
    try:
        cache = HashCache.ensure(cache_path, report)
    except OSError as exc:
        report(f"Switch: hash cache unavailable ({exc})")
        return []

    try:
        archive = open_archive(source, report)
    except Exception as exc:
        report(f"Switch: Trinity archive unavailable ({exc})")
        return []

    assets: list[Asset] = []
    patterns = (
        ("pokemon/data/", ".trmdl", "Switch Pokémon model", "TRMD"),
        ("pokemon/data/", ".bntx", "Switch Pokémon texture", "BNTX"),
        ("pokemon/data/", ".tranm", "Switch Pokémon animation", "TRAN"),
        ("pokemon/data/", ".gfbanm", "Switch Pokémon animation", "TRAN"),
        ("map/", ".trmdl", "Switch map model", "TRMD"),
        ("map/", ".bntx", "Switch map texture", "BNTX"),
        ("charamodel/", ".trmdl", "Switch character model", "TRMD"),
        ("charamodel/", ".bntx", "Switch character texture", "BNTX"),
        ("icon/", ".bntx", "Switch icon", "BNTX"),
        ("poke_icon/", ".bntx", "Switch icon", "BNTX"),
    )
    seen: set[str] = set()
    for prefix, suffix, kind, magic in patterns:
        for trpak, inner, file_hash in iter_named_trinity_files(
            archive,
            cache,
            suffix=suffix,
            contains=prefix,
        ):
            key = f"{trpak}|{inner}"
            if key in seen:
                continue
            seen.add(key)
            clean = inner.replace("/", "_")
            assets.append(
                _descriptor_asset(
                    asset_id=f"switch_trinity_{clean}",
                    virtual_path=f"switch/{source.stem}/{inner}",
                    kind=kind,
                    magic=magic,
                    payload={
                        "type": "trinity_file",
                        "rom": str(source),
                        "trpak_path": trpak,
                        "inner_path": inner,
                        "file_hash": file_hash,
                    },
                    mapping_label=inner,
                )
            )
    report(f"Switch: Trinity index — {len(assets)} browsable assets")
    return assets


_MODEL_EXT = ".trmdl"
_TEXTURE_EXT = ".bntx"
_ANIM_EXTS = (".tranm", ".gfbanm")
_ICON_HINTS = ("icon", "poke_icon", "pm_icon")


def _classify(path: str) -> tuple[str, str, str]:
    """Return (kind, magic, extension) for a loose RomFS file."""
    lower = path.lower()
    ext = Path(lower).suffix
    if lower.endswith(_MODEL_EXT):
        return ("Switch model", "TRMD", _MODEL_EXT)
    if lower.endswith(_TEXTURE_EXT):
        return ("Switch texture", "BNTX", _TEXTURE_EXT)
    if any(lower.endswith(a) for a in _ANIM_EXTS):
        return ("Switch animation", "TRAN", ext)
    if lower.endswith(".trpfs") or lower.endswith(".trpfd"):
        return ("Switch Trinity pack", "TRPF", ext)
    if lower.endswith(".bnk") or lower.endswith(".pck"):
        return ("Switch audio (Wwise)", "WWSE", ext)
    if lower.endswith(".bk2"):
        return ("Switch movie (Bink)", "BIK2", ext)
    return (f"Switch file ({ext or 'bin'})", "SWFL", ext or ".bin")


def scan_switch_rom_path(
    path: str | Path,
    progress: Callable[[str], None] | None = None,
    **_kwargs,
) -> list[Asset]:
    source = Path(path).expanduser().resolve()

    def report(message: str) -> None:
        if progress:
            progress(message)

    report(f"Switch: opening {source.name}…")

    if find_prod_keys(source) is None:
        report("Switch: prod.keys not found — ROM stays locked")
        return [
            _locked_row(
                source,
                "prod.keys not found. Dump it from your console with Lockpick_RCM "
                "and place it next to the ROM, in ~/.switch/, or set "
                "RAE_SWITCH_PROD_KEYS. Then rescan to browse models, textures and "
                "animations.",
            )
        ]

    try:
        keys = SwitchKeys.load(source)
    except SwitchKeyError as exc:
        return [_locked_row(source, str(exc))]

    # Imported lazily so a missing pycryptodome doesn't break platform import.
    from .container import SwitchContainerError, SwitchRom

    assets: list[Asset] = []
    try:
        with SwitchRom(source, keys) as rom:
            report("Switch: decrypting Program NCA RomFS…")
            files = rom.romfs_files()
            report(f"Switch: RomFS parsed — {len(files)} files")

            assets.append(
                _descriptor_asset(
                    asset_id="switch_rom_summary",
                    virtual_path=f"switch/{source.stem}/rae_switch_rom.json",
                    kind="Switch ROM summary",
                    magic="SWRM",
                    payload={
                        "type": "summary",
                        "rom": str(source),
                        "romfs_files": len(files),
                        "keys_source": str(keys.source),
                    },
                    mapping_label=f"{source.stem} — {len(files)} files",
                )
            )

            counts: dict[str, int] = {}
            for entry in files:
                kind, magic, ext = _classify(entry.path)
                is_icon = any(hint in entry.path.lower() for hint in _ICON_HINTS)
                clean_id = entry.path.strip("/").replace("/", "_")
                assets.append(
                    _descriptor_asset(
                        asset_id=f"switch_{clean_id}",
                        virtual_path=f"switch/{source.stem}{entry.path}",
                        kind=("Switch icon" if is_icon and magic == "BNTX" else kind),
                        magic=magic,
                        payload={
                            "type": "romfs_file",
                            "rom": str(source),
                            "romfs_path": entry.path,
                            "offset": entry.offset,
                            "size": entry.size,
                            "is_icon": is_icon,
                        },
                        mapping_label=entry.path,
                    )
                )
                counts[magic] = counts.get(magic, 0) + 1
            assets.extend(_scan_trinity_assets(source, report))
            report(f"Switch: scan complete — {len(assets)} rows {counts}")
    except SwitchContainerError as exc:
        return [_locked_row(source, f"{source.name}: {exc}")]

    return assets
