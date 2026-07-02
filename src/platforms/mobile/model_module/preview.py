from __future__ import annotations

import json
from pathlib import Path

from ....install import project_root
from ...home.assetstudio_preview import assetstudio_available
from ...home.home_textures import texture_sheet_entries
from ..mesh_export import export_first_mesh_preview_glb, unitypy_available


def build_mobile_asset_preview(asset, output_dir: Path, *, progress) -> dict:
    if getattr(asset, "magic", "") == "ABA":
        from ...home.aba_preview import build_aba_asset_preview

        return build_aba_asset_preview(asset, output_dir, progress=progress)

    payload = _asset_payload(asset)
    bundle_candidates: list[str] = []
    preferred_names: list[str] = []

    if getattr(asset, "magic", "") == "UNITY":
        local_path = payload.get("local_path") or payload.get("localPath")
        if local_path:
            bundle_candidates.append(str(local_path))
        preferred_names.append(
            Path(payload.get("virtual_path") or payload.get("virtualPath") or getattr(asset, "virtual_path", "mesh")).stem
        )
    elif getattr(asset, "magic", "") == "HOME":
        # Grouped HOME species rows: try the readable HOME Cache before touching
        # the encrypted per-form .aba bundles.
        try:
            return _build_home_package_cache_preview(asset, payload, output_dir, progress=progress)
        except Exception as exc:
            progress(f"HOME Cache preview unavailable: {exc}")
        preferred_names.extend([str(payload.get("id") or ""), str(payload.get("name") or "")])
        source_rows = payload.get("source_files") or payload.get("sourceFiles") or []
        for row in source_rows:
            local_path = row.get("local_path") or row.get("localPath")
            magic = row.get("magic", "")
            if local_path and magic in {"UnityFS", "UnityWeb", "UnityRaw"}:
                bundle_candidates.append(str(local_path))
        if not bundle_candidates and _home_rows_all_encrypted(source_rows):
            species = str(payload.get("name") or payload.get("id") or "this species")
            raise RuntimeError(
                f"{species} is locked: its model only exists as key-encrypted .aba stubs in "
                "this ROM extract. Open the Pokémon once in the HOME app (any form), then use "
                "Device Toolkit → Extract Installed App ROM to refresh — it will appear under "
                "'ready to preview'."
            )
        if not bundle_candidates:
            for row in source_rows:
                local_path = row.get("local_path") or row.get("localPath")
                if local_path:
                    bundle_candidates.append(str(local_path))

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in bundle_candidates:
        path = str(Path(candidate).expanduser())
        if path not in seen and Path(path).exists():
            deduped.append(path)
            seen.add(path)

    if not deduped:
        raise RuntimeError("Selected asset does not contain a readable local Unity bundle path to preview.")

    progress(f"Trying {len(deduped)} mobile bundle candidate(s) for preview…")
    last_error = None
    for candidate in deduped:
        progress(f"Inspecting Unity bundle for geometry preview: {candidate}")
        if unitypy_available():
            try:
                result = export_first_mesh_preview_glb(candidate, output_dir, preferred_names=preferred_names)
                return {
                    "glb_path": result.glb_path,
                    "mesh_name": result.mesh_name,
                    "source_bundle": result.source_bundle,
                    "mesh_count": result.mesh_count,
                    "warnings": result.warnings,
                    "preview_backend": "unitypy",
                }
            except Exception as exc:
                last_error = exc
                progress(f"UnityPy preview failed: {exc}")
        if assetstudio_available():
            try:
                from ...home.assetstudio_preview import export_mesh_preview_glb

                result = export_mesh_preview_glb(candidate, output_dir, preferred_names=preferred_names)
                return {
                    "glb_path": result.glb_path,
                    "mesh_name": result.mesh_name,
                    "source_bundle": result.source_bundle,
                    "mesh_count": result.mesh_count,
                    "warnings": result.warnings,
                    "preview_backend": "assetstudio",
                    "texture_paths": list(result.texture_paths),
                    "texture_by_name": dict(result.texture_by_name),
                    "material_to_texture": dict(result.material_to_texture),
                    "texture_bind_order": list(result.texture_bind_order),
                    "texture_sheet_entries": texture_sheet_entries(
                        [Path(path) for path in result.texture_paths]
                    ),
                }
            except Exception as exc:
                last_error = exc
                progress(f"AssetStudio preview failed: {exc}")

    raise RuntimeError(f"Could not build a previewable model from the selected asset: {last_error}")


def _home_rows_all_encrypted(source_rows: list) -> bool:
    """True when every source file of a HOME package is an encrypted .aba stub."""
    if not source_rows:
        return False
    for row in source_rows:
        virtual = str(row.get("virtual_path") or row.get("virtualPath") or "")
        if not virtual.casefold().endswith((".aba", ".abap")):
            return False
    return True


def _build_home_package_cache_preview(asset, payload: dict, output_dir: Path, *, progress) -> dict:
    """Preview a grouped HOME species row straight from the readable HOME Cache."""
    from ...home.aba_preview import _mobile_rom_root
    from ...home.assetstudio_preview import (
        assetstudio_available,
        export_home_species_from_cache,
        home_cache_directories,
    )
    from ...home.home_textures import texture_sheet_entries as home_texture_sheet_entries
    from ...home.ids import parse_home_asset_id

    if not assetstudio_available():
        raise RuntimeError("AssetStudioModCLI is not configured.")

    parsed = parse_home_asset_id(str(payload.get("id") or "")) or parse_home_asset_id(
        str(getattr(asset, "virtual_path", "") or "")
    )
    if parsed is None:
        raise RuntimeError("No species id on this HOME package row.")

    mobile_root = _mobile_rom_root(asset)
    cache_dirs = home_cache_directories(mobile_root)
    if not cache_dirs:
        raise RuntimeError("No HOME Cache folder in this ROM extract.")

    progress(f"Trying HOME Cache for species #{parsed.number:04d}…")
    result = export_home_species_from_cache(cache_dirs, parsed, output_dir)
    return {
        "glb_path": result.glb_path,
        "mesh_name": result.mesh_name,
        "source_bundle": result.source_bundle,
        "mesh_count": result.mesh_count,
        "warnings": result.warnings,
        "preview_backend": "assetstudio-cache",
        "home_cache_dirs": [str(path) for path in cache_dirs],
        "texture_paths": list(result.texture_paths),
        "texture_by_name": dict(result.texture_by_name),
        "material_to_texture": dict(result.material_to_texture),
        "texture_bind_order": list(result.texture_bind_order),
        "texture_sheet_entries": home_texture_sheet_entries(
            [Path(path) for path in result.texture_paths]
        ),
    }


def _asset_payload(asset) -> dict:
    raw = getattr(asset, "data", b"")
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    else:
        text = str(raw)
    try:
        return json.loads(text)
    except Exception as exc:
        raise RuntimeError(f"Selected asset does not carry JSON payload data: {exc}") from exc
