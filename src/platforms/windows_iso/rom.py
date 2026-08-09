"""Marine Park Empire Windows ISO catalog scanner.

The scanner returns one summary row and one row per SMO/AM1 model.  AM2/AM3
animation and DDS/TGA texture members remain metadata records attached to their
model descriptor, keeping the browser useful while enabling lazy extraction.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

from ...core.assets import Asset
from .container import (
    InstallShieldEntry,
    WindowsIsoContainerError,
    cache_installshield_cabinets,
    extract_installshield_member,
    find_7z,
    find_unshield,
    list_installshield_entries,
    list_iso_entries,
)
from .profiles import WindowsIsoProfile, identify_profile, profile_hint_from_filename
from .resources import ModelBinding, model_bindings


MODEL_EXTENSIONS = {".smo", ".am1"}
ANIMATION_EXTENSIONS = {".am2", ".am3"}
TEXTURE_EXTENSIONS = {".dds", ".tga"}


def load_descriptor(asset: Asset) -> dict:
    try:
        value = json.loads(asset.data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def scan_windows_iso_rom_path(
    path: str | Path,
    progress: Callable[[str], None] | None = None,
    **kwargs,
) -> list[Asset]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if source.suffix.casefold() != ".iso":
        raise ValueError(f"Windows CD source must be an ISO image: {source}")

    def report(message: str) -> None:
        if progress:
            progress(message)

    report(f"Windows ISO: inspecting {source.name}…")
    seven_zip = find_7z()
    if not seven_zip:
        return [_information_asset(
            source,
            profile_hint_from_filename(source),
            "7-Zip was not found. Install 7-Zip/p7zip and ensure the 7z or 7zz "
            "command is on PATH, then rescan the disc.",
            locked=True,
        )]

    try:
        iso_entries = list_iso_entries(source, seven_zip)
    except WindowsIsoContainerError as exc:
        return [_information_asset(source, profile_hint_from_filename(source), str(exc), locked=True)]

    profile = identify_profile(iso_entries)
    if profile is None:
        return [_information_asset(
            source,
            None,
            "This Windows ISO is not a recognized RAE game profile yet. The first "
            "supported profile is Marine Park Empire (2005).",
            locked=False,
        )]
    report(f"Windows ISO: identified {profile.title} ({profile.year})")

    unshield = find_unshield()
    if not unshield:
        return [_information_asset(
            source,
            profile,
            "unshield was not found. Install unshield or set RAE_UNSHIELD to its "
            "executable, then rescan to browse the InstallShield model catalog.",
            locked=True,
        )]

    try:
        cache_root_value = kwargs.get("cache_root")
        cache_root = Path(cache_root_value) if cache_root_value is not None else None
        report("Windows ISO: caching InstallShield cabinets (first scan may take a moment)…")
        cache_dir = cache_installshield_cabinets(
            source,
            iso_entries,
            seven_zip,
            member_names=profile.installer_members,
            cache_root=cache_root,
        )
        report("Windows ISO: reading InstallShield catalog…")
        catalog = list_installshield_entries(cache_dir, unshield)
    except (OSError, WindowsIsoContainerError) as exc:
        return [_information_asset(source, profile, str(exc), locked=True)]

    bindings: dict[str, ModelBinding] = {}
    by_name = {PurePosixPath(entry.path).name.casefold(): entry for entry in catalog}
    model_resource = by_name.get("model.res")
    group_resource = by_name.get("animgrp.res")
    if model_resource and group_resource:
        try:
            report("Windows ISO: linking authoritative animation groups and shadows…")
            bindings = model_bindings(
                extract_installshield_member(cache_dir, model_resource.path, unshield).read_bytes(),
                extract_installshield_member(cache_dir, group_resource.path, unshield).read_bytes(),
            )
        except (OSError, WindowsIsoContainerError):
            bindings = {}
    assets = _catalog_assets(source, profile, cache_dir, catalog, bindings=bindings)
    report(f"Windows ISO: {len(assets) - 1:,} model rows ready (catalog metadata only)")
    return assets


def _catalog_assets(
    source: Path,
    profile: WindowsIsoProfile,
    cache_dir: Path,
    catalog: Iterable[InstallShieldEntry],
    *,
    bindings: dict[str, ModelBinding] | None = None,
) -> list[Asset]:
    records = list(catalog)
    models = [entry for entry in records if _is_browsable_model(entry)]
    primary_models, model_variants = _primary_model_records(models)
    animations = [entry for entry in records if entry.extension in ANIMATION_EXTENSIONS]
    textures = [entry for entry in records if entry.extension in TEXTURE_EXTENSIONS]

    animation_index = _dependency_index(animations)
    texture_index = _dependency_index(textures)
    model_index = _dependency_index(models)
    linked_animations: set[str] = set()
    linked_textures: set[str] = set()
    model_assets: list[Asset] = []
    for entry in sorted(primary_models, key=lambda item: item.path.casefold()):
        exact_binding_key = PurePosixPath(entry.path).stem.upper()
        family_binding_key = _family_stem(PurePosixPath(entry.path).stem).upper()
        binding = (bindings or {}).get(exact_binding_key) or (bindings or {}).get(family_binding_key)
        related_animations = _related_records(entry, animation_index)
        if binding:
            bound = [item for item in animations
                     if PurePosixPath(item.path).stem.casefold() in {
                         name.casefold() for name in binding.animation_stems
                     }]
            related_animations = _unique_records([*related_animations, *bound])
        alias_stems = _animation_alias_stems(PurePosixPath(entry.path).stem)
        if alias_stems:
            aliased = [item for item in animations
                       if PurePosixPath(item.path).stem.casefold() in alias_stems]
            related_animations = _unique_records([*related_animations, *aliased])
        related_textures = _related_records(entry, texture_index)
        shadows: list[InstallShieldEntry] = []
        if binding:
            for shadow_name in (binding.simple_shadow, binding.detail_shadow):
                if not shadow_name or shadow_name.upper() == "NO SHADOW":
                    continue
                shadow_stem = PurePosixPath(shadow_name).stem.casefold()
                shadows.extend(model_index[0].get(shadow_stem, []))
            shadows = _unique_records(shadows)
        variants = model_variants.get(entry.path.casefold(), {})
        shadows = _unique_records([*shadows, *variants.get("shadows", [])])
        linked_animations.update(item.path.casefold() for item in related_animations)
        linked_textures.update(item.path.casefold() for item in related_textures)
        model_assets.append(
            _model_asset(
                source,
                profile,
                cache_dir,
                entry,
                related_animations,
                related_textures,
                shadows,
                variants.get("lods", []),
            )
        )

    counts = Counter(entry.extension.upper().lstrip(".") for entry in records)
    summary = _summary_asset(
        source,
        profile,
        cache_dir,
        catalog_count=len(records),
        model_count=len(primary_models),
        counts=counts,
        unassociated_animation_count=sum(
            entry.path.casefold() not in linked_animations for entry in animations
        ),
        unassociated_texture_count=sum(
            entry.path.casefold() not in linked_textures for entry in textures
        ),
    )
    return [summary, *model_assets]


def _is_browsable_model(entry: InstallShieldEntry) -> bool:
    if entry.extension not in MODEL_EXTENSIONS:
        return False
    # The installer contains empty sentinels named as models plus one AM1 wave
    # effect. They have no usable model geometry and should not clutter a model
    # browser or produce knowingly invalid Models Resource submissions.
    stem = PurePosixPath(entry.path).stem.casefold()
    if entry.size <= 16 or stem in {"null", "null1", "wave"}:
        return False
    return True


def _primary_model_records(
    models: Iterable[InstallShieldEntry],
) -> tuple[list[InstallShieldEntry], dict[str, dict[str, list[InstallShieldEntry]]]]:
    """Collapse V3D LOD and projected-shadow records into their primary row."""
    groups: dict[tuple[str, str, str], list[InstallShieldEntry]] = defaultdict(list)
    for entry in models:
        source = PurePosixPath(entry.path)
        key = (source.parent.as_posix().casefold(), _render_family_stem(source.stem), entry.extension)
        groups[key].append(entry)

    primary: list[InstallShieldEntry] = []
    variants: dict[str, dict[str, list[InstallShieldEntry]]] = {}
    for (_, family, _), rows in groups.items():
        visible = [row for row in rows if not PurePosixPath(
            row.path,
        ).stem.casefold().endswith("#s")]
        if not visible:
            # A projected-shadow record is not a useful standalone model.
            continue
        unsuffixed = [row for row in visible if _render_family_stem(
            PurePosixPath(row.path).stem,
        ) == PurePosixPath(row.path).stem.casefold()]
        # A few incomplete families contain only a numbered record. Keep the
        # largest available mesh browsable rather than silently dropping it.
        chosen = max(unsuffixed or visible, key=lambda row: (row.size, row.path.casefold()))
        primary.append(chosen)
        lods = [row for row in rows if row is not chosen and re.search(
            r"#[23]$", PurePosixPath(row.path).stem, re.IGNORECASE,
        )]
        shadows = [row for row in rows if row is not chosen and PurePosixPath(
            row.path,
        ).stem.casefold().endswith("#s")]
        variants[chosen.path.casefold()] = {
            "lods": sorted(lods, key=lambda row: row.path.casefold()),
            "shadows": sorted(shadows, key=lambda row: row.path.casefold()),
        }
    return primary, variants


def _dependency_index(
    entries: Iterable[InstallShieldEntry],
) -> tuple[dict[str, list[InstallShieldEntry]], dict[str, list[InstallShieldEntry]]]:
    exact: dict[str, list[InstallShieldEntry]] = defaultdict(list)
    family: dict[str, list[InstallShieldEntry]] = defaultdict(list)
    for entry in entries:
        stem = PurePosixPath(entry.path).stem.casefold()
        exact[stem].append(entry)
        family[_family_stem(stem)].append(entry)
    return exact, family


def _related_records(
    model: InstallShieldEntry,
    index: tuple[dict[str, list[InstallShieldEntry]], dict[str, list[InstallShieldEntry]]],
) -> list[InstallShieldEntry]:
    exact, family = index
    stem = PurePosixPath(model.path).stem.casefold()
    # Keep exact records and family records together. Variant AM1 meshes often
    # have their own AM3 hierarchy but intentionally share the base AM2 clips.
    matches = [*exact.get(stem, []), *family.get(_family_stem(stem), [])]
    # Catalogs can contain case-only duplicates; expose a path only once.
    unique = {item.path.casefold(): item for item in matches}
    return sorted(unique.values(), key=lambda item: item.path.casefold())


def _unique_records(rows: Iterable[InstallShieldEntry]) -> list[InstallShieldEntry]:
    unique = {row.path.casefold(): row for row in rows}
    return sorted(unique.values(), key=lambda item: item.path.casefold())


_VARIANT_SUFFIX = re.compile(r"(?:#[23s]|#s|_binding|-bind)$", re.IGNORECASE)
_MODEL_RENDER_SUFFIX = re.compile(r"#[23s]$", re.IGNORECASE)
_MODEL_RENDER_ALIASES = {
    "sshark": "sawshark",
    "ssharks": "sawsharks",
}

_ANIMATION_ALIASES: dict[str, tuple[str, ...]] = {
    "jfishs#": ("jfishs",),
    "northern_fur_sealf": ("northern_fur_seal_female",),
    "seaotters#s": ("seaotter_baby",),
    "spinner_dolphin": ("dolphin_spinner",),
    "sshark": ("sawshark",),
    "ssharks": ("sawsharks",),
    "striped_dolphin": ("dolphin_striped",),
}


def _family_stem(stem: str) -> str:
    return _VARIANT_SUFFIX.sub("", stem).casefold()


def _render_family_stem(stem: str) -> str:
    family = _MODEL_RENDER_SUFFIX.sub("", stem).casefold()
    return _MODEL_RENDER_ALIASES.get(family, family)


def _animation_alias_stems(stem: str) -> set[str]:
    exact = stem.casefold()
    family = _family_stem(stem)
    values = [*_ANIMATION_ALIASES.get(exact, ()), *_ANIMATION_ALIASES.get(family, ())]
    return {value.casefold() for value in values}


def _model_asset(
    source: Path,
    profile: WindowsIsoProfile,
    cache_dir: Path,
    model: InstallShieldEntry,
    animations: list[InstallShieldEntry],
    textures: list[InstallShieldEntry],
    shadows: list[InstallShieldEntry],
    lods: list[InstallShieldEntry],
) -> Asset:
    model_format = model.extension.upper().lstrip(".")
    payload = {
        "schema": "rae-windows-iso-model-v1",
        "type": "model",
        "profile_id": profile.profile_id,
        "game_title": profile.title,
        "rom": str(source),
        "cache_dir": str(cache_dir),
        "cabinet": "data1.cab",
        "model": _record(model),
        "animations": [_record(entry) for entry in animations],
        "textures": [_record(entry) for entry in textures],
        "shadows": [_record(entry) for entry in shadows],
        "lods": [_record(entry) for entry in lods],
    }
    digest = hashlib.sha1(model.path.casefold().encode("utf-8")).hexdigest()[:14]
    magic = "WSMO" if model.extension == ".smo" else "WAM1"
    kind = "Marine Park Empire static model" if magic == "WSMO" else "Marine Park Empire animated model"
    return _descriptor_asset(
        asset_id=f"windows_iso_model_{digest}",
        virtual_path=f"windows_iso/{profile.title}/{model.path}",
        kind=kind,
        magic=magic,
        payload=payload,
        mapping_category="models",
        mapping_label=f"{PurePosixPath(model.path).name} ({model_format})",
        mapping_confidence="installshield-catalog",
    )


def _summary_asset(
    source: Path,
    profile: WindowsIsoProfile,
    cache_dir: Path,
    *,
    catalog_count: int,
    model_count: int,
    counts: Counter[str],
    unassociated_animation_count: int,
    unassociated_texture_count: int,
) -> Asset:
    payload = {
        "schema": "rae-windows-iso-summary-v1",
        "type": "summary",
        "profile_id": profile.profile_id,
        "game_title": profile.title,
        "year": profile.year,
        "rom": str(source),
        "cache_dir": str(cache_dir),
        "catalog_records": catalog_count,
        "model_rows": model_count,
        "format_counts": dict(sorted(counts.items())),
        "unassociated_animation_records": unassociated_animation_count,
        "unassociated_texture_records": unassociated_texture_count,
    }
    return _descriptor_asset(
        asset_id="windows_iso_marine_park_empire_summary",
        virtual_path=f"windows_iso/{profile.title}/rae_disc_summary.json",
        kind="Marine Park Empire disc summary",
        magic="WISO",
        payload=payload,
        mapping_category="windows_iso",
        mapping_label=f"{profile.title} ({profile.year}) — {model_count:,} models",
        mapping_confidence="iso-signature",
    )


def _information_asset(
    source: Path,
    profile: WindowsIsoProfile | None,
    message: str,
    *,
    locked: bool,
) -> Asset:
    payload = {
        "schema": "rae-windows-iso-information-v1",
        "type": "locked" if locked else "information",
        "profile_id": profile.profile_id if profile else "",
        "game_title": profile.title if profile else "",
        "rom": str(source),
        "message": message,
    }
    return _descriptor_asset(
        asset_id="windows_iso_setup_required" if locked else "windows_iso_information",
        virtual_path=f"windows_iso/{source.stem}/rae_information.json",
        kind="Windows ISO setup required" if locked else "Windows ISO information",
        magic="WILK",
        payload=payload,
        mapping_category="windows_iso",
        mapping_label=(profile.title if profile else source.stem),
        mapping_confidence="filename-hint" if profile else "unrecognized",
    )


def _descriptor_asset(
    *,
    asset_id: str,
    virtual_path: str,
    kind: str,
    magic: str,
    payload: dict,
    mapping_category: str,
    mapping_label: str,
    mapping_confidence: str,
) -> Asset:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return Asset(
        asset_id=asset_id,
        virtual_path=virtual_path,
        kind=kind,
        magic=magic,
        extension=".json",
        data=data,
        original_data=data,
        compressed=False,
        container_chain=("iso", "installshield"),
        mapping_category=mapping_category,
        mapping_label=mapping_label,
        mapping_confidence=mapping_confidence,
    )


def _record(entry: InstallShieldEntry) -> dict[str, object]:
    return {
        "path": entry.path,
        "size": entry.size,
        "format": entry.extension.upper().lstrip("."),
    }
