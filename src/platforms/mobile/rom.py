
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable

from ..android.source import AndroidSourceFile, scan_android_source
from ..home.library import build_home_library, home_packages_as_rae_assets
from ...core.registry import Platform
from ...core.assets import Asset
from .archive import is_mobile_rom_archive, resolve_mobile_rom_root
from ..home.mapping import apply_home_mapping_to_assets, choose_mapping_for_mobile_source
from ...core.mapping import load_mappings

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
    source = Path(path).expanduser().resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if not source.is_dir() and not is_mobile_rom_archive(source):
        raise ValueError(f"Mobile .rom sources must be zip archives or directory bundles: {source}")

    root = resolve_mobile_rom_root(source)
    manifest = read_mobile_rom_manifest(root)
    if progress:
        progress(f"Mobile ROM: {manifest.app_name or source.stem} ({manifest.package_id or 'unknown package'})")

    assets: list[Asset] = [_summary_asset(source, root, manifest)]

    # Pokémon HOME gets a higher-level package builder that groups every form,
    # texture, and animation for a species into a single browsable row.
    covered_species: set[str] = set()
    is_home = (
        manifest.package_id.casefold() == POKEMON_HOME_PACKAGE
        or "pokemon_home" in source.stem.casefold()
        or "pokemonhome" in source.stem.casefold()
    )
    if is_home:
        assets.append(_home_ui_assets_asset(source, root, manifest))
        try:
            if progress:
                progress("Building Pokémon HOME form package index from mobile ROM…")
            lib = build_home_library(root, progress=progress, inspect_unity=True)
            package_assets = home_packages_as_rae_assets(lib)
            assets.extend(package_assets)
            covered_species = {pkg.id.casefold() for pkg in lib.packages}
            inventory_path = root / "home_inventory.json"
            lib.write_json(inventory_path)
            if progress:
                progress(f"Pokémon HOME packages: {len(package_assets):,}; inventory: {inventory_path}")
        except Exception as exc:
            if progress:
                progress(f"Pokémon HOME package index failed; continuing with raw mobile assets: {exc}")

    inv = scan_android_source(root, progress=progress)
    raw_entries = _dedupe_android_entries(inv.files, covered_species=covered_species, is_home=is_home)
    for entry in raw_entries:
        assets.append(_asset_from_android_entry(source, root, manifest, entry))
    if progress:
        collapsed = len(inv.files) - len(raw_entries)
        if collapsed > 0:
            progress(f"Collapsed {collapsed:,} duplicate/grouped raw file row(s) into HOME packages.")
        progress(f"Mobile ROM scan complete: {len(assets):,} browsable asset row(s).")

    mapping = choose_mapping_for_mobile_source(
        package_id=manifest.package_id,
        title=manifest.source_label or manifest.app_name,
        source_stem=source.stem,
    )
    if mapping is None:
        for candidate in load_mappings(platform="mobile"):
            if candidate.game_family == "pokemon_home":
                mapping = candidate
                break
    apply_home_mapping_to_assets(assets, mapping)
    if mapping is not None and mapping.game_family == "pokemon_home":
        try:
            from ..home.species_index import ensure_cache_species_index

            if progress:
                progress("Indexing previewable Pokémon HOME Cache species (first scan may take ~30s)…")
            previewable = ensure_cache_species_index(root, progress=progress)
            _group_home_assets_by_availability(assets, previewable)
            apply_home_mapping_to_assets(assets, mapping)
            if progress and previewable:
                progress(
                    f"HOME: {len(previewable):,} species are previewable from Cache; "
                    "they are listed under 'ready to preview' in the tree."
                )
        except Exception as exc:
            if progress:
                progress(f"HOME species cache index skipped: {exc}")
    if progress and mapping is not None:
        progress(f"Applied mapping: {mapping.mapping_id}")
    return assets


_CACHE_INTERNAL_TOKENS = ("/cache/", "unityshadercache", "/il2cpp/", "/unity/")

_HOME_READY_FOLDER = "1 ready to preview (in HOME Cache)"
_HOME_LOCKED_FOLDER = "2 locked (view in HOME app to unlock)"


def _group_home_assets_by_availability(assets: list[Asset], previewable: set[int]) -> None:
    """Split HOME species rows into 'ready to preview' vs 'locked' tree folders.

    Cache-ready species preview instantly from the readable HOME Cache; the rest
    only have key-locked .aba stubs until the Pokémon is viewed in the HOME app.
    """
    from ..home.ids import parse_home_asset_id

    for asset in assets:
        magic = getattr(asset, "magic", "")
        if magic not in {"HOME", "ABA"}:
            continue
        virtual_path = str(getattr(asset, "virtual_path", "") or "")
        parsed = parse_home_asset_id(virtual_path)
        if parsed is None:
            continue
        folder = _HOME_READY_FOLDER if parsed.number in previewable else _HOME_LOCKED_FOLDER
        parts = virtual_path.split("/")
        # Keep the platform/app prefix segments in place, then insert the group folder.
        insert_at = 2 if len(parts) > 2 and parts[0] == "mobile" else 1
        insert_at = min(insert_at, len(parts) - 1)
        asset.virtual_path = "/".join([*parts[:insert_at], folder, *parts[insert_at:]])


def _dedupe_android_entries(
    entries: list[AndroidSourceFile],
    *,
    covered_species: set[str],
    is_home: bool,
) -> list[AndroidSourceFile]:
    """Collapse the "gazillion identical rows" a HOME ROM produces.

    A HOME ``.rom`` ships the same per-species files under ``external_files/``,
    a mirrored ``candidates/`` tree, and inside ``base.apk``. Every form/shiny
    ``.aba`` is also already represented by its grouped ``.homepkg`` species row.
    We keep one row per unique (basename, size), drop species files that the
    package index already covers, and hide Unity cache/shader internals.
    """
    if not is_home:
        return entries

    from ..home.ids import parse_home_asset_id

    kept: list[AndroidSourceFile] = []
    seen_files: set[tuple[str, int]] = set()
    for entry in entries:
        low = entry.virtual_path.casefold()
        name = entry.virtual_path.rsplit("/", 1)[-1].split("!/")[-1]

        # Species files (cap####, mt_pv_ev_####, pm####) are folded into the
        # grouped HOME package row, so they are pure duplicates in the browser.
        parsed = parse_home_asset_id(name)
        if parsed is not None and parsed.canonical.casefold() in covered_species:
            continue

        # Unity engine caches / shader caches / il2cpp are not user assets.
        if any(token in low for token in _CACHE_INTERNAL_TOKENS):
            continue

        dedupe_key = (name.casefold(), int(entry.size))
        if dedupe_key in seen_files:
            continue
        seen_files.add(dedupe_key)
        kept.append(entry)
    return kept


def _summary_asset(source: Path, root: Path, manifest: MobileRomManifest) -> Asset:
    payload = {
        "kind": "mobile-rom-summary",
        "source": str(source),
        "root": str(root),
        "manifest": manifest.to_dict(),
        "notes": [
            "A .rom mobile source is a single zip archive. RAE unpacks it to a local cache while browsing.",
            "Use Device Toolkit → Mobile → Extract Installed App ROM… to regenerate this bundle from an Android device.",
        ],
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    return Asset(
        asset_id="mobile_rom_summary",
        virtual_path=f"mobile/{source.name}/rae_mobile_rom.json",
        kind="Mobile app ROM summary",
        magic="MOBL",
        extension=".json",
        data=data,
        original_data=data,
        mapping_category="mobile",
        mapping_label=f"{manifest.app_name or source.stem} mobile ROM ({manifest.package_id or 'unknown package'})",
        mapping_confidence="mobile-rom-manifest",
    )


def _home_ui_assets_asset(source: Path, root: Path, manifest: MobileRomManifest) -> Asset:
    """Browsable row for the readable HOME app UI assets inside base.apk."""
    payload = {
        "kind": "home-ui-assets",
        "root": str(root),
        "mobileRom": {
            "source": str(source),
            "root": str(root),
            "packageId": manifest.package_id,
            "appName": manifest.app_name,
        },
        "notes": [
            "base.apk ships the Unity player data folder unencrypted.",
            "Export this row to extract UI icons, sprites (incl. Pokéball/menu art), fonts, and audio.",
        ],
    }
    data = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
    return Asset(
        asset_id="home_ui_assets",
        virtual_path=f"pokemon_home/0 app UI assets (icons, sprites, fonts, audio).homeui",
        kind="Pokémon HOME app UI assets",
        magic="HOMEUI",
        extension=".json",
        data=data,
        original_data=data,
        mapping_category="textures/materials",
        mapping_label="HOME app UI assets — icons, sprites, fonts, audio (export to extract)",
        mapping_confidence="home-apk-data",
    )


def _asset_from_android_entry(source: Path, root: Path, manifest: MobileRomManifest, entry: AndroidSourceFile) -> Asset:
    row = entry.to_dict()
    row["mobileRom"] = {
        "source": str(source),
        "root": str(root),
        "packageId": manifest.package_id,
        "appName": manifest.app_name,
    }
    payload = json.dumps(row, indent=2, ensure_ascii=False).encode("utf-8")
    digest = hashlib.sha1((entry.virtual_path + str(entry.size) + entry.partial_sha256).encode("utf-8")).hexdigest()[:12]
    magic = _magic_for_entry(entry)
    return Asset(
        asset_id=f"mobile_{digest}",
        virtual_path=f"mobile/{manifest.app_name or source.stem}/{entry.virtual_path}",
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


def export_mobile_asset(asset: Asset, out: Path) -> Path:
    """Write mobile asset payload (metadata or extracted bytes) to *out*."""
    target = Path(out)
    if target.is_dir():
        name = Path(asset.virtual_path).name or f"{asset.asset_id}{asset.extension}"
        target = target / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(asset.data or b"")
    return target


def export_mobile_readable(asset: Asset, out: Path) -> list[Path]:
    """Best-effort readable export for recognized mobile asset signatures."""
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    return [export_mobile_asset(asset, out_dir)]
