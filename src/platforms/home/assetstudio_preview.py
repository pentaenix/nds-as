from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from ..mobile.mesh_export import (
    UnityMeshPreviewResult,
    _merge_obj_models,
    _ObjModel,
    _parse_obj,
    _safe_name,
    _split_obj_by_groups,
    _write_glb,
)
from .aba import decrypt_aba_bytes, decrypt_aba_to_cache
from .home_textures import build_home_texture_bindings, export_home_textures, stage_textures_beside_glb, align_home_bindings_to_glb
from .ids import HomePokemonId

_ASSETSTUDIO_FILTER_RE = re.compile(
    r"Found \[(?P<found>\d+)/(?P<total>\d+)\] asset\(s\) that contain .(?P<filter>[^.\n]+). in their Names",
    re.IGNORECASE,
)
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text)


def assetstudio_available() -> bool:
    return resolve_assetstudio_cli() is not None


def resolve_assetstudio_cli() -> Path | None:
    env = os.environ.get("RAE_ASSETSTUDIO_CLI", "").strip()
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return path.resolve()
        if path.is_dir():
            for name in ("AssetStudioModCLI", "AssetStudioModCLI.dll"):
                candidate = path / name
                if candidate.exists():
                    return candidate.resolve()

    from ...install import project_root

    for candidate in (
        project_root() / "tools" / "AssetStudioModCLI" / "AssetStudioModCLI",
        project_root() / "tools" / "AssetStudioModCLI" / "AssetStudioModCLI.dll",
        project_root() / "tools" / "AssetStudioModCLI_net9_portable" / "AssetStudioModCLI.dll",
    ):
        if candidate.exists():
            return candidate.resolve()
    return None


def home_cache_directories(mobile_root: Path | None) -> list[Path]:
    if mobile_root is None:
        return []
    root = mobile_root.expanduser().resolve()
    dirs: list[Path] = []
    for pattern in (
        "external_files/files/Cache",
        "files/Cache",
        "candidates/files/Cache",
    ):
        candidate = root / pattern
        if candidate.is_dir():
            dirs.append(candidate.resolve())
    return dirs


def species_mesh_filter(parsed: HomePokemonId) -> str:
    return f"pm{parsed.number:04d}_{parsed.form_a}_{parsed.form_b}"


def count_cache_meshes_for_filter(cache_dir: Path, name_filter: str) -> int:
    output = _run_assetstudio(
        [str(cache_dir), "-m", "info", "-t", "Mesh", "--load-all", "--filter-by-name", name_filter],
        allow_failure=True,
    )
    for line in output.splitlines():
        match = _ASSETSTUDIO_FILTER_RE.search(line)
        if match:
            return int(match.group("found"))
    if "Nothing exported" in output or "No Unity file can be loaded" in output:
        return 0
    return 0


_FORM_PREFIX_RE = re.compile(r"^(pm\d{4}_\d{2}_\d{2})", re.IGNORECASE)


def _sibling_form_meshes(primary: Path, all_objs: list[Path]) -> list[Path]:
    """Primary OBJ plus every other exported mesh of the same pm####_##_## form."""
    match = _FORM_PREFIX_RE.match(primary.stem)
    if match is None:
        return [primary]
    prefix = match.group(1).casefold()
    siblings = [primary]
    seen = {primary.stem.casefold()}
    for path in sorted(all_objs, key=lambda p: p.stem.casefold()):
        stem = path.stem.casefold()
        if stem in seen or not stem.startswith(prefix):
            continue
        seen.add(stem)
        siblings.append(path)
    return siblings


def export_mesh_preview_glb(
    bundle_path: str | Path,
    output_dir: str | Path,
    *,
    name_filter: str | None = None,
    preferred_names: list[str] | None = None,
    export_textures: bool = True,
) -> UnityMeshPreviewResult:
    cli = resolve_assetstudio_cli()
    if cli is None:
        raise RuntimeError(
            "AssetStudioModCLI is required for Pokémon HOME Unity 6 bundle previews. "
            "Download AssetStudioModCLI_net9_portable from https://github.com/aelurum/AssetStudio/releases "
            "and set RAE_ASSETSTUDIO_CLI to the CLI folder, or unpack it to rae/tools/AssetStudioModCLI/."
        )

    bundle = Path(bundle_path).expanduser().resolve()
    if not bundle.exists():
        raise FileNotFoundError(bundle)

    with tempfile.TemporaryDirectory(prefix="rae-ascli-") as tmp:
        tmp_dir = Path(tmp)
        args = [str(bundle), "-m", "export", "-t", "Mesh", "-o", str(tmp_dir)]
        if name_filter:
            args.extend(["--filter-by-name", name_filter])
        _run_assetstudio(args)

        obj_files = sorted(tmp_dir.rglob("*.obj"), key=lambda path: path.stat().st_size, reverse=True)
        if not obj_files:
            hint = (
                f'AssetStudio did not export any meshes from "{bundle.name}". '
                "HOME tyranitar .aba stubs often use block-level encryption; "
                "open the Pokémon in HOME to populate external_files/files/Cache, then re-extract the ROM."
            )
            if name_filter:
                hint += f' No mesh names matched filter "{name_filter}".'
            raise RuntimeError(hint)

        preferred = [p.casefold() for p in (preferred_names or []) if p]

        def rank(path: Path) -> tuple[int, int, str]:
            stem = path.stem.casefold()
            best = 9999
            for idx, pref in enumerate(preferred):
                if pref and pref in stem:
                    best = idx
                    break
            body_bonus = 0 if "body" in stem else 1
            return (best, body_bonus, stem)

        obj_path = sorted(obj_files, key=rank)[0]

        # A species/form is often split across several meshes (BodySkin +
        # FeelerSkin wings, …). Merge every mesh of the primary mesh's form so
        # the model isn't missing whole parts.
        sibling_paths = _sibling_form_meshes(obj_path, obj_files)
        parsed_models: list[tuple[str, _ObjModel]] = []
        for path in sibling_paths:
            candidate = _parse_obj(path.read_text(encoding="utf-8", errors="replace"))
            if candidate.positions and candidate.faces:
                parsed_models.append((path.stem, candidate))
        if not parsed_models:
            raise RuntimeError(f"Exported OBJ {obj_path.name} did not contain usable geometry.")
        model = parsed_models[0][1] if len(parsed_models) == 1 else _merge_obj_models(parsed_models)

        group_parts = _split_obj_by_groups(model)
        part_names = [name for name, part in group_parts if part.faces]

        output_root = Path(output_dir).expanduser().resolve()
        output_root.mkdir(parents=True, exist_ok=True)
        mesh_name = obj_path.stem
        glb_path = output_root / f"{_safe_name(f'{bundle.stem}__{mesh_name}')}.glb"
        _write_glb(model, glb_path, mesh_name=mesh_name)
        texture_paths: list[Path] = []
        texture_by_name: dict[str, str] = {}
        material_to_texture: dict[str, str] = {}
        texture_bind_order: list[str] = []
        if export_textures:
            try:
                texture_paths = export_home_textures(bundle, tmp_dir, name_filter=name_filter)
                bindings = build_home_texture_bindings(mesh_name, texture_paths, part_names=part_names)
                staged = stage_textures_beside_glb(bindings.texture_paths, glb_path)
                texture_paths = staged
                bindings = align_home_bindings_to_glb(
                    glb_path,
                    bindings,
                    texture_paths=staged,
                )
                from ...core.preview.texture_paths import texture_map_from_paths

                texture_by_name = {key: str(path) for key, path in texture_map_from_paths(staged).items()}
                material_to_texture = dict(bindings.material_to_texture)
                texture_bind_order = list(bindings.texture_bind_order)
            except Exception as exc:
                warnings = [f"texture export failed: {exc}"]
            else:
                warnings = []
        else:
            warnings = []
        return UnityMeshPreviewResult(
            source_bundle=str(bundle),
            mesh_name=mesh_name,
            glb_path=str(glb_path),
            mesh_count=len(obj_files),
            warnings=warnings,
            texture_paths=[str(path) for path in texture_paths],
            texture_by_name=texture_by_name,
            material_to_texture=material_to_texture,
            texture_bind_order=texture_bind_order,
        )


def export_home_species_from_cache(
    cache_dirs: list[Path],
    parsed: HomePokemonId,
    output_dir: str | Path,
) -> UnityMeshPreviewResult:
    if not cache_dirs:
        raise RuntimeError("No HOME Cache directory was found in this mobile ROM.")

    filters = [
        f"pm{parsed.number:04d}",
        species_mesh_filter(parsed),
        f"cap{parsed.number:04d}",
    ]
    last_error: Exception | None = None
    for cache_dir in cache_dirs:
        for name_filter in filters:
            if count_cache_meshes_for_filter(cache_dir, name_filter) <= 0:
                continue
            try:
                return export_mesh_preview_glb(
                    cache_dir,
                    output_dir,
                    name_filter=name_filter,
                    preferred_names=[species_mesh_filter(parsed), f"pm{parsed.number:04d}", "Body"],
                )
            except Exception as exc:
                last_error = exc
    raise RuntimeError(
        f"No previewable meshes for species #{parsed.number:04d} were found in HOME Cache on this ROM. "
        "View the Pokémon in HOME to download model data, then re-extract the app ROM."
    ) from last_error


def prepare_bundle_for_assetstudio(path: Path, cache_dir: Path) -> Path:
    source = path.expanduser().resolve()
    if source.suffix.casefold() == ".aba":
        return decrypt_aba_to_cache(source, cache_dir)
    return source


def _run_assetstudio(args: list[str], *, allow_failure: bool = False) -> str:
    cli = resolve_assetstudio_cli()
    if cli is None:
        raise RuntimeError("AssetStudioModCLI is not configured.")

    env = os.environ.copy()
    env.setdefault("DOTNET_ROLL_FORWARD", "LatestMajor")

    if cli.suffix.casefold() == ".dll":
        command = ["dotnet", str(cli), *args]
        cwd = cli.parent
    else:
        command = [str(cli), *args]
        cwd = cli.parent if cli.parent.exists() else None

    completed = subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = _strip_ansi("\n".join(part for part in (completed.stdout, completed.stderr) if part))
    if completed.returncode != 0 and not allow_failure:
        detail = output.strip() or f"exit code {completed.returncode}"
        raise RuntimeError(f"AssetStudioModCLI failed:\n{detail}")
    return output
