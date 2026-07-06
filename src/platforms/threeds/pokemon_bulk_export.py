"""Bulk Pokémon GLB export for supported 3DS titles (uses the normal export pipeline)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

from ...core.assets import Asset
from .game_catalog import is_ultra_moon_rom, read_product_code
from .rom import load_descriptor

Progress = Callable[[str], None]


@dataclass(slots=True)
class PokemonBulkExportResult:
    written: list[Path] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    manifest_path: Path | None = None
    species_count: int = 0


def pokemon_bulk_export_available(rom_path: str | Path, assets: Iterable[Asset]) -> bool:
    """True when *rom_path* is Ultra Moon and the scan has GFMD rows."""
    if not is_ultra_moon_rom(rom_path):
        return False
    return any(asset.magic == "GFMD" for asset in assets)


def pokemon_bulk_export_assets(assets: Iterable[Asset]) -> list[Asset]:
    """One GFMD row per species (form 00) — ``build_model_glb`` bundles every form."""
    seen_species: set[int] = set()
    selected: list[Asset] = []
    for asset in assets:
        if asset.magic != "GFMD":
            continue
        path = asset.virtual_path.replace("\\", "/")
        if "/form_00/" not in path:
            continue
        descriptor = load_descriptor(asset)
        if not descriptor:
            continue
        species = int(descriptor.get("species") or 0)
        if species <= 0 or species in seen_species:
            continue
        seen_species.add(species)
        selected.append(asset)
    selected.sort(
        key=lambda item: int((load_descriptor(item) or {}).get("species") or 0),
    )
    return selected


def run_pokemon_bulk_export(
    host: object,
    assets: Iterable[Asset],
    rom_path: str | Path,
    out_dir: str | Path,
    *,
    shiny: bool = False,
    progress: Progress | None = None,
    export_choice: Callable[..., list[Path]] | None = None,
) -> PokemonBulkExportResult:
    """Export every species GLB via the same path as File → Export Selected → GLB."""
    from .export_module import ThreedsExportModule

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    candidates = pokemon_bulk_export_assets(assets)
    if not candidates:
        raise ValueError("No Pokémon model rows (form 00) were found in this scan.")

    exporter = ThreedsExportModule()
    run = export_choice or exporter.run_export_choice
    host_shiny = bool(getattr(host, "_export_glb_shiny", False))
    if shiny or host_shiny:
        setattr(host, "_export_glb_shiny", True)
    else:
        setattr(host, "_export_glb_shiny", False)

    result = PokemonBulkExportResult(species_count=len(candidates))
    total = len(candidates)
    for index, asset in enumerate(candidates, start=1):
        descriptor = load_descriptor(asset) or {}
        species = int(descriptor.get("species") or 0)
        label = descriptor.get("name") or asset.virtual_path
        if progress:
            progress(f"Pokémon bulk export {index}/{total}: #{species:04d} {label}")
        try:
            paths = run(host, asset, "glb", out)
            result.written.extend(paths)
        except Exception as exc:
            result.errors.append(
                {
                    "species": species,
                    "virtual_path": asset.virtual_path,
                    "error": str(exc),
                }
            )

    manifest = {
        "format": "rae-threeds-pokemon-bulk-export-v1",
        "game": "Pokémon Ultra Moon",
        "product_code": read_product_code(rom_path),
        "rom": str(Path(rom_path).expanduser().resolve()),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "species_requested": total,
        "files_written": len(result.written),
        "errors": result.errors,
        "default_texture_variant": "shiny" if getattr(host, "_export_glb_shiny", False) else "normal",
        "outputs": [path.name for path in result.written],
    }
    manifest_path = out / "pokemon_bulk_export_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    result.manifest_path = manifest_path
    return result
