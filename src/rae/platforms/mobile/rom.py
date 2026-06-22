
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..android.source import AndroidSourceFile, scan_android_source
from ..home.library import build_home_library, home_packages_as_rae_assets
from ...core.registry import Platform
from ...platforms.nds.scanner import Asset

MOBILE_ROM_MANIFEST = "rae_mobile_rom.json"
POKEMON_HOME_PACKAGE = "jp.pokemon.pokemonhome"


@dataclass(slots=True)
class MobileRomManifest:
    format: str = "rae-mobile-app-rom-v1"
    platform: str = "android"
    package_id: str = ""
    app_name: str = ""
    source_label: str = ""
    fetched_at: str = ""
    device_serial: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def read_mobile_rom_manifest(path: str | Path) -> MobileRomManifest:
    root = Path(path).expanduser().resolve()
    manifest_path = root / MOBILE_ROM_MANIFEST
    if manifest_path.exists():
        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            return MobileRomManifest(
                format=str(raw.get("format") or raw.get("raeRomFormat") or "rae-mobile-app-rom-v1"),
                platform=str(raw.get("platform") or raw.get("sourceKind") or "android"),
                package_id=str(raw.get("packageId") or raw.get("package_id") or ""),
                app_name=str(raw.get("appName") or raw.get("app_name") or root.stem),
                source_label=str(raw.get("sourceLabel") or raw.get("source_label") or root.name),
                fetched_at=str(raw.get("fetchedAt") or raw.get("fetched_at") or ""),
                device_serial=str(raw.get("deviceSerial") or raw.get("device_serial") or ""),
            )
        except Exception:
            pass
    return MobileRomManifest(app_name=root.stem, source_label=root.name)


def scan_mobile_rom_path(
    path: str | Path,
    progress: Callable[[str], None] | None = None,
    **_kwargs,
) -> list[Asset]:
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if not root.is_dir():
        raise ValueError(f"Mobile .rom sources are directory bundles, not regular files: {root}")

    manifest = read_mobile_rom_manifest(root)
    if progress:
        progress(f"Mobile ROM: {manifest.app_name or root.stem} ({manifest.package_id or 'unknown package'})")

    assets: list[Asset] = [_summary_asset(root, manifest)]

    # Pokémon HOME gets a higher-level package builder, but the lower-level
    # file inventory is still emitted so a bad/missing package grouping never
    # leaves the browser empty.
    package_id = manifest.package_id.casefold()
    if package_id == POKEMON_HOME_PACKAGE or "pokemon_home" in root.name.casefold() or "pokemonhome" in root.name.casefold():
        try:
            if progress:
                progress("Building Pokémon HOME form package index from mobile ROM…")
            lib = build_home_library(root, progress=progress, inspect_unity=True)
            package_assets = home_packages_as_rae_assets(lib)
            assets.extend(package_assets)
            inventory_path = root / "home_inventory.json"
            lib.write_json(inventory_path)
            if progress:
                progress(f"Pokémon HOME packages: {len(package_assets):,}; inventory: {inventory_path}")
        except Exception as exc:
            if progress:
                progress(f"Pokémon HOME package index failed; continuing with raw mobile assets: {exc}")

    inv = scan_android_source(root, progress=progress)
    for entry in inv.files:
        assets.append(_asset_from_android_entry(root, manifest, entry))
    if progress:
        progress(f"Mobile ROM scan complete: {len(assets):,} browsable asset row(s).")
    return assets


def _summary_asset(root: Path, manifest: MobileRomManifest) -> Asset:
    payload = {
        "kind": "mobile-rom-summary",
        "root": str(root),
        "manifest": manifest.to_dict(),
        "notes": [
            "A .rom mobile source is a directory bundle. RAE treats it like a ROM root while keeping the app-specific files inside.",
            "Use Device Toolkit → Mobile → Extract Installed App ROM… to regenerate this bundle from an Android device.",
        ],
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    return Asset(
        asset_id="mobile_rom_summary",
        virtual_path=f"mobile/{root.name}/rae_mobile_rom.json",
        kind="Mobile app ROM summary",
        magic="MOBL",
        extension=".json",
        data=data,
        original_data=data,
        mapping_category="mobile",
        mapping_label=f"{manifest.app_name or root.stem} mobile ROM ({manifest.package_id or 'unknown package'})",
        mapping_confidence="mobile-rom-manifest",
    )


def _asset_from_android_entry(root: Path, manifest: MobileRomManifest, entry: AndroidSourceFile) -> Asset:
    row = entry.to_dict()
    row["mobileRom"] = {
        "root": str(root),
        "packageId": manifest.package_id,
        "appName": manifest.app_name,
    }
    payload = json.dumps(row, indent=2, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha1((entry.virtual_path + str(entry.size) + entry.partial_sha256).encode("utf-8")).hexdigest()[:12]
    magic = _magic_for_entry(entry)
    return Asset(
        asset_id=f"mobile_{digest}",
        virtual_path=f"mobile/{manifest.app_name or root.stem}/{entry.virtual_path}",
        kind=_kind_for_entry(entry),
        magic=magic,
        extension=entry.extension or ".bin",
        data=payload,
        original_data=payload,
        mapping_category=_category_for_entry(entry, magic),
        mapping_label=_label_for_entry(entry, magic),
        mapping_confidence="mobile-rom-inventory",
    )


def _magic_for_entry(entry: AndroidSourceFile) -> str:
    classification = entry.classification.casefold()
    if classification == "unity-readable-bundle" or entry.magic in {"UnityFS", "UnityWeb", "UnityRaw"}:
        return "UNITY"
    if "aba" in classification or entry.extension in {".aba", ".abap"}:
        return "ABA"
    if "metadata" in classification or entry.extension in {".json", ".manifest"}:
        return "MOBL"
    if "home" in classification:
        return "HOME"
    return "MOBL"


def _kind_for_entry(entry: AndroidSourceFile) -> str:
    cls = entry.classification.replace("-", " ")
    if cls:
        return cls.title()
    return "Mobile source file"


def _category_for_entry(entry: AndroidSourceFile, magic: str) -> str:
    text = f"{entry.virtual_path} {entry.classification}".casefold()
    if magic == "UNITY" or any(token in text for token in ("mesh", "model", "pokemons/pm", "/pokemons/")):
        return "models"
    if any(token in text for token in ("texture", "dependencies", "material")):
        return "textures/materials"
    if "anim" in text:
        return "animations"
    if magic == "ABA":
        return "packages"
    return "mobile"


def _label_for_entry(entry: AndroidSourceFile, magic: str) -> str:
    base = entry.virtual_path.rsplit("/", 1)[-1] or entry.virtual_path
    if magic == "UNITY":
        return f"Readable Unity candidate: {base}"
    if magic == "ABA":
        return f"Encrypted/packaged mobile asset candidate: {base}"
    if magic == "HOME":
        return f"Pokémon HOME candidate: {base}"
    return f"Mobile asset candidate: {base}"


MOBILE_PLATFORM = Platform(
    id="mobile",
    label="Mobile app ROM",
    rom_extensions=(".rom",),
    status="active",
    scan_rom_path=scan_mobile_rom_path,
    mapping_platform="mobile",
)
