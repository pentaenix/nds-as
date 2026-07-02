from __future__ import annotations

import json
import zipfile
from pathlib import Path

from ...install import project_root
from ..mobile.mesh_export import export_first_mesh_preview_glb, unitypy_available
from .aba import decrypt_aba_to_cache, is_likely_unity_bundle, probe_aba_decrypt
from .assetstudio_preview import (
    assetstudio_available,
    export_home_species_from_cache,
    export_mesh_preview_glb,
    home_cache_directories,
    prepare_bundle_for_assetstudio,
    species_mesh_filter,
)
from .home_textures import texture_sheet_entries
from .ids import parse_home_asset_id


def resolve_local_aba_path(asset) -> Path | None:
    return materialize_aba_source(asset)


def materialize_aba_source(asset) -> Path | None:
    payload = _asset_payload(asset)
    local_path = payload.get("local_path") or payload.get("localPath")
    if local_path:
        path = Path(str(local_path)).expanduser()
        if path.is_file():
            return path.resolve()

    container = payload.get("container")
    member = _zip_member_path(payload, asset)
    if container and member:
        container_path = Path(str(container)).expanduser()
        if container_path.is_file() and zipfile.is_zipfile(container_path):
            cache_dir = project_root() / ".cache" / "home-aba" / "apk-members"
            return _extract_zip_member(container_path, member, cache_dir)

    virtual_path = str(payload.get("virtual_path") or payload.get("virtualPath") or getattr(asset, "virtual_path", ""))
    if "!/" in virtual_path:
        container_name, member = virtual_path.split("!/", 1)
        mobile_root = _mobile_rom_root(asset)
        if mobile_root is not None:
            for container_candidate in _container_candidates(mobile_root, container_name):
                if container_candidate.is_file() and zipfile.is_zipfile(container_candidate):
                    cache_dir = project_root() / ".cache" / "home-aba" / "apk-members"
                    return _extract_zip_member(container_candidate, member.lstrip("/"), cache_dir)

    name = Path(virtual_path.split("!/", 1)[-1]).name
    mobile_root = _mobile_rom_root(asset)
    if mobile_root is not None:
        root = Path(mobile_root)
        for pattern in (f"**/tyranitar/{name}", f"**/files/tyranitar/{name}", f"**/{name}"):
            matches = sorted(root.glob(pattern))
            for candidate in matches:
                if candidate.is_file():
                    return candidate.resolve()
    return None


def related_home_aba_paths(path: Path, *, mobile_root: Path | None = None) -> list[Path]:
    parsed = parse_home_asset_id(path.name)
    if parsed is None:
        return [path.resolve()]

    token = f"{parsed.number:04d}"
    found: dict[str, Path] = {path.resolve().name: path.resolve()}

    search_roots: list[Path] = []
    if mobile_root is not None:
        search_roots.append(Path(mobile_root).expanduser().resolve())
    parent_root = path.parent
    if parent_root not in search_roots:
        search_roots.append(parent_root)

    patterns = (
        f"cap{token}*.aba",
        f"mt_pv_ev_{token}*.aba",
        f"pm{token}*.aba",
        f"*{token}*.aba",
    )
    for root in search_roots:
        for folder in (root / "external_files/files/tyranitar", root / "candidates/files/tyranitar", root):
            if not folder.is_dir():
                continue
            for pattern in patterns:
                for candidate in folder.glob(pattern):
                    if candidate.is_file():
                        found[candidate.name] = candidate.resolve()

    apk_members = _apk_aba_members_for_token(search_roots, token)
    for member_path in apk_members:
        found[member_path.name] = member_path

    ordered = [found[path.name] for path in [path.resolve()] if path.name in found]
    for key in sorted(found):
        if found[key] not in ordered:
            ordered.append(found[key])
    return ordered or [path.resolve()]


def build_aba_asset_preview(asset, output_dir: Path, *, progress) -> dict:
    if not unitypy_available() and not assetstudio_available():
        raise RuntimeError(
            "HOME ABA previews need UnityPy and/or AssetStudioModCLI.\n"
            "Install UnityPy: python -m pip install UnityPy\n"
            "Install AssetStudioModCLI_net9_portable and set RAE_ASSETSTUDIO_CLI."
        )

    source = materialize_aba_source(asset)
    if source is None:
        raise RuntimeError(
            "Selected ABA asset does not have a readable local file path. "
            "If this row lives inside base.apk, RAE could not locate the container APK on disk."
        )

    mobile_root = _mobile_rom_root(asset)
    parsed = parse_home_asset_id(source.name) or parse_home_asset_id(
        str(getattr(asset, "virtual_path", "") or "")
    )
    cache_dir = project_root() / ".cache" / "home-aba" / (mobile_root.name if mobile_root else source.parent.name)
    candidates = related_home_aba_paths(source, mobile_root=mobile_root)
    progress(f"Resolved {len(candidates)} related HOME ABA file(s) for preview…")

    preferred: list[str] = [source.stem, Path(str(getattr(asset, "virtual_path", ""))).stem]
    if parsed is not None:
        preferred.extend(
            [
                parsed.canonical,
                species_mesh_filter(parsed),
                f"pm{parsed.number:04d}",
                f"cap{parsed.number:04d}",
                f"mt_pv_ev_{parsed.number:04d}",
            ]
        )

    probes: list[dict] = []
    last_error: Exception | None = None

    if parsed is not None and assetstudio_available():
        cache_dirs = home_cache_directories(
            Path(mobile_root) if mobile_root is not None else source.parent.parent.parent.parent
        )
        if cache_dirs:
            from .species_index import ensure_cache_species_index

            if mobile_root is not None:
                ensure_cache_species_index(Path(mobile_root), progress=progress)
            progress(
                f"Trying HOME Cache first for species #{parsed.number:04d} "
                f"({len(cache_dirs)} cache folder(s))…"
            )
            try:
                result = export_home_species_from_cache(cache_dirs, parsed, output_dir)
                return _preview_result(
                    result,
                    source=source,
                    candidates=candidates,
                    probes=probes,
                    backend="assetstudio-cache",
                    cache_dirs=cache_dirs,
                )
            except Exception as exc:
                last_error = exc
                progress(f"Cache species preview failed: {exc}")

    for candidate in candidates:
        progress(f"Decrypting {candidate.name}…")
        probes.append(probe_aba_decrypt(candidate))
        try:
            decrypted = prepare_bundle_for_assetstudio(candidate, cache_dir)
        except Exception as exc:
            last_error = exc
            progress(f"Decrypt failed for {candidate.name}: {exc}")
            continue

        if unitypy_available():
            progress(f"Trying UnityPy mesh preview: {decrypted.name}")
            try:
                result = export_first_mesh_preview_glb(decrypted, output_dir, preferred_names=preferred)
                return _preview_result(
                    result,
                    source=source,
                    candidates=candidates,
                    probes=probes,
                    backend="unitypy",
                )
            except Exception as exc:
                last_error = exc
                progress(f"UnityPy preview failed: {exc}")

        if assetstudio_available():
            progress(f"Trying AssetStudio mesh preview: {decrypted.name}")
            try:
                result = export_mesh_preview_glb(decrypted, output_dir, preferred_names=preferred)
                return _preview_result(
                    result,
                    source=source,
                    candidates=candidates,
                    probes=probes,
                    backend="assetstudio",
                )
            except Exception as exc:
                last_error = exc
                progress(f"AssetStudio bundle preview failed: {exc}")

    hint = (
        "No previewable mesh was found for this species. "
        "Tyranitar .aba stubs use block-level encryption that RAE cannot decrypt yet; "
        "view the Pokémon once in HOME (any form/shiny is fine) to populate external_files/files/Cache, "
        "then re-extract the ROM. Cached species preview from HOME Cache does not require viewing every shiny."
    )
    if parsed is not None:
        hint += f" Species #{parsed.number:04d} was not present in Cache on this ROM extract."
    raise RuntimeError(f"{hint}\n\nLast error: {last_error}")


def _preview_result(
    result,
    *,
    source: Path,
    candidates: list[Path],
    probes: list[dict],
    backend: str,
    cache_dirs: list[Path] | None = None,
) -> dict:
    texture_paths = [str(path) for path in getattr(result, "texture_paths", []) or []]
    texture_by_name = dict(getattr(result, "texture_by_name", {}) or {})
    material_to_texture = dict(getattr(result, "material_to_texture", {}) or {})
    texture_bind_order = list(getattr(result, "texture_bind_order", []) or [])
    payload = {
        "glb_path": result.glb_path,
        "mesh_name": result.mesh_name,
        "source_bundle": result.source_bundle,
        "mesh_count": result.mesh_count,
        "warnings": result.warnings,
        "aba_source": str(source),
        "aba_related": [str(path) for path in candidates],
        "aba_probes": probes,
        "preview_backend": backend,
        "texture_paths": texture_paths,
        "texture_by_name": texture_by_name,
        "material_to_texture": material_to_texture,
        "texture_bind_order": texture_bind_order,
        "texture_sheet_entries": texture_sheet_entries([Path(path) for path in texture_paths]),
    }
    if cache_dirs:
        payload["home_cache_dirs"] = [str(path) for path in cache_dirs]
    return payload


def _extract_zip_member(container: Path, member: str, cache_dir: Path) -> Path:
    member_path = member.replace("\\", "/").lstrip("/")
    safe_name = Path(member_path).name
    out_dir = cache_dir / container.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / safe_name
    with zipfile.ZipFile(container) as zf:
        try:
            payload = zf.read(member_path)
        except KeyError:
            alt = next(
                (name for name in zf.namelist() if name.rstrip("/").endswith(safe_name)),
                None,
            )
            if alt is None:
                raise
            payload = zf.read(alt)
    target.write_bytes(payload)
    return target.resolve()


def _zip_member_path(payload: dict, asset) -> str | None:
    virtual_path = str(payload.get("virtual_path") or payload.get("virtualPath") or getattr(asset, "virtual_path", ""))
    if "!/" in virtual_path:
        return virtual_path.split("!/", 1)[1].lstrip("/")
    return None


def _container_candidates(mobile_root: Path, container_name: str) -> list[Path]:
    name = Path(container_name).name
    return [
        mobile_root / "apk" / name,
        mobile_root / name,
        mobile_root / "apk" / "base.apk",
    ]


def _apk_aba_members_for_token(search_roots: list[Path], token: str) -> list[Path]:
    extracted: list[Path] = []
    cache_dir = project_root() / ".cache" / "home-aba" / "apk-members"
    for root in search_roots:
        for apk in (root / "apk" / "base.apk", root / "base.apk"):
            if not apk.is_file() or not zipfile.is_zipfile(apk):
                continue
            with zipfile.ZipFile(apk) as zf:
                for name in zf.namelist():
                    base = Path(name).name
                    if not base.endswith(".aba"):
                        continue
                    if token in base or f"cap{token}" in base or f"_{token}_" in base:
                        extracted.append(_extract_zip_member(apk, name, cache_dir))
    return extracted


def _mobile_rom_root(asset) -> Path | None:
    mobile_root = getattr(asset, "mobileRom", None)
    if isinstance(mobile_root, dict):
        root = mobile_root.get("root") or mobile_root.get("source")
        if root:
            return Path(str(root)).expanduser().resolve()
    payload = _asset_payload(asset)
    payload_mobile = payload.get("mobileRom") or payload.get("mobile_rom")
    if isinstance(payload_mobile, dict):
        root = payload_mobile.get("root") or payload_mobile.get("source")
        if root:
            root_path = Path(str(root)).expanduser()
            if root_path.is_dir():
                return root_path.resolve()
    local_path = payload.get("local_path") or payload.get("localPath")
    if local_path:
        derived = _mobile_root_from_local_path(Path(str(local_path)).expanduser())
        if derived is not None:
            return derived
    for row in payload.get("source_files") or payload.get("sourceFiles") or []:
        row_local = row.get("local_path") or row.get("localPath")
        if row_local:
            derived = _mobile_root_from_local_path(Path(str(row_local)).expanduser())
            if derived is not None:
                return derived
    container = payload.get("container")
    if container:
        container_path = Path(str(container)).expanduser()
        if container_path.is_file():
            # .../pokemon_home/apk/base.apk -> pokemon_home
            if container_path.parent.name == "apk":
                return container_path.parent.parent.resolve()
            return container_path.parent.resolve()
    return None


def _mobile_root_from_local_path(local_path: Path) -> Path | None:
    """Walk up from an extracted file to the mobile ROM root (has rae_mobile_rom.json / apk)."""
    current = local_path if local_path.is_dir() else local_path.parent
    for candidate in [current, *current.parents]:
        if (candidate / "rae_mobile_rom.json").is_file() or (candidate / "apk").is_dir():
            return candidate.resolve()
        # extracted layouts: <root>/external_files/files/... or <root>/candidates/files/...
        if candidate.name in {"external_files", "candidates"} and candidate.parent.is_dir():
            return candidate.parent.resolve()
    return None


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
