"""Self-contained Pokémon HOME model export (GLB + shiny + animations).

The reliable source for HOME models is the readable ``external_files/files/Cache``
Unity bundle set, which AssetStudioModCLI can load without any UnityCN key. This
module turns a species' Cache assets into a portable export folder:

- ``<species>.glb``        — mesh with normals + embedded normal-color textures
- ``<species>_shiny.glb``  — same mesh with the shiny/rare textures (when cached)
- ``textures/``            — every exported ``_col``/``_emi`` PNG (normal + shiny)
- ``<species>_animations.fbx`` — AssetStudio FBX with model animations (best-effort)
- ``home_model_export.json`` — manifest describing what was produced / skipped

Shiny colouring in HOME is a texture swap that only appears in the Cache once the
Pokémon has been viewed as shiny in the app. When the shiny texture is not
present (or the animation clips live only in the key-locked ``mt_pv_ev`` .aba),
the manifest records that honestly rather than inventing data.
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ...glb_policy.embed_textures import embed_glb_external_images
from ...glb_policy.glb_io import read_glb
from ...glb_policy.texture_patch import write_patched_preview_glb
from .assetstudio_preview import (
    _run_assetstudio,
    assetstudio_available,
    export_home_species_from_cache,
    resolve_assetstudio_cli,
    species_mesh_filter,
)
from .home_textures import export_home_textures
from .ids import HomePokemonId, pokemon_display_name

# Texture stems that indicate a shiny / rare / alternate-colour variant.
_SHINY_TEXTURE_RE = re.compile(r"(rare|shiny|_r$|_r_|col2|_2_col|colr\b)", re.IGNORECASE)


@dataclass(slots=True)
class HomeModelExport:
    species_id: str
    species_name: str
    glb_path: str
    shiny_glb_path: str | None = None
    texture_dir: str | None = None
    animation_fbx_path: str | None = None
    texture_paths: list[str] = field(default_factory=list)
    shiny_texture_paths: list[str] = field(default_factory=list)
    animation_clip_paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "speciesId": self.species_id,
            "speciesName": self.species_name,
            "glb": self.glb_path,
            "shinyGlb": self.shiny_glb_path,
            "textureDir": self.texture_dir,
            "animationFbx": self.animation_fbx_path,
            "textures": self.texture_paths,
            "shinyTextures": self.shiny_texture_paths,
            "animationClips": self.animation_clip_paths,
            "notes": self.notes,
        }


def _is_shiny_texture(path: Path) -> bool:
    return bool(_SHINY_TEXTURE_RE.search(path.stem))


def export_home_model_package(
    cache_dirs: list[Path],
    parsed: HomePokemonId,
    output_dir: str | Path,
    *,
    progress=None,
) -> HomeModelExport:
    """Produce a portable model export folder for one HOME species from Cache."""

    def _say(message: str) -> None:
        if progress:
            progress(message)

    if not assetstudio_available():
        raise RuntimeError(
            "HOME model export needs AssetStudioModCLI. Download "
            "AssetStudioModCLI_net9_portable and set RAE_ASSETSTUDIO_CLI, or unpack it "
            "to rae/tools/AssetStudioModCLI/."
        )
    if not cache_dirs:
        raise RuntimeError(
            "No readable HOME Cache directory was found. View the Pokémon in HOME to "
            "download its model, then re-extract the app ROM."
        )

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    species_name = pokemon_display_name(parsed.number)
    notes: list[str] = []

    # 1) Mesh + textures from Cache (AssetStudio; no UnityCN key required).
    _say(f"Exporting {species_name} mesh from HOME Cache…")
    preview = export_home_species_from_cache(cache_dirs, parsed, out_root)
    source_glb = Path(preview.glb_path)

    # Gather every texture for the species across all cache folders (normal + shiny).
    all_textures: dict[str, Path] = {}
    for cache_dir in cache_dirs:
        for name_filter in (species_mesh_filter(parsed), f"pm{parsed.number:04d}", f"cap{parsed.number:04d}"):
            try:
                for tex in export_home_textures(cache_dir, out_root / "textures", name_filter=name_filter):
                    all_textures[tex.name.casefold()] = tex
            except Exception as exc:  # pragma: no cover - best effort per filter
                notes.append(f"texture export ({name_filter}) skipped: {exc}")

    # The broad pm#### filter can drag in other forms' sheets (pm0012_01_00_…).
    # Prefer the exact form's sheet for each part, but keep another form's sheet
    # when this form doesn't have that part cached (forms often share sheets).
    all_textures = _prefer_exact_form_textures(all_textures, parsed)

    texture_paths = sorted(all_textures.values(), key=lambda p: p.name.casefold())
    normal_textures = [p for p in texture_paths if not _is_shiny_texture(p)]
    shiny_textures = [p for p in texture_paths if _is_shiny_texture(p)]

    # 2) Self-contained main GLB with embedded normal-colour textures.
    _say("Binding textures and embedding into a portable GLB…")
    main_glb = out_root / f"{parsed.canonical}.glb"
    _build_textured_glb(source_glb, main_glb, normal_textures or texture_paths)

    export = HomeModelExport(
        species_id=parsed.canonical,
        species_name=species_name,
        glb_path=str(main_glb),
        texture_dir=str((out_root / "textures").resolve()) if texture_paths else None,
        texture_paths=[str(p) for p in normal_textures],
        shiny_texture_paths=[str(p) for p in shiny_textures],
        notes=notes,
    )

    # 3) Shiny GLB when a shiny/rare colour texture is present in the Cache.
    if shiny_textures:
        _say("Shiny textures found — building shiny GLB…")
        shiny_glb = out_root / f"{parsed.canonical}_shiny.glb"
        # Prefer shiny col textures but fall back to normal for parts without a shiny map.
        shiny_set = _merge_texture_sets(base=normal_textures, override=shiny_textures)
        _build_textured_glb(source_glb, shiny_glb, shiny_set)
        export.shiny_glb_path = str(shiny_glb)
    else:
        notes.append(
            "No shiny/alternate-colour texture is present in this ROM's HOME Cache for "
            f"#{parsed.number:04d}. View the shiny form once in Pokémon HOME and re-extract "
            "the app ROM to include it (the encrypted shiny .aba needs a UnityCN key RAE does not ship)."
        )

    # 4) Animations. The Cache CABs carry AnimationClips (wait/attack/roar…) but no
    #    Animator/GameObject hierarchy, so AssetStudio cannot bind them into an FBX.
    #    Export readable clip dumps so the animation data ships with the package,
    #    and still attempt the FBX in case a full hierarchy is present.
    fbx = _export_animation_fbx(cache_dirs, parsed, out_root, progress=progress)
    if fbx is not None:
        export.animation_fbx_path = str(fbx)
    clip_dumps = _export_animation_clip_dumps(cache_dirs, parsed, out_root, progress=progress)
    export.animation_clip_paths = [str(p) for p in clip_dumps]
    if clip_dumps:
        notes.append(
            f"{len(clip_dumps)} animation clip dump(s) exported to animations/ "
            "(Unity muscle-clip data; the Cache has no Animator hierarchy, so RAE "
            "cannot bind them into the GLB/FBX yet)."
        )
    if fbx is None and not clip_dumps:
        notes.append(
            "No animation clips were readable in the HOME Cache for this species. HOME "
            "keeps animations in encrypted mt_pv_ev .aba packages that require a UnityCN "
            "key RAE cannot supply; the static posed mesh is exported instead."
        )

    manifest = out_root / "home_model_export.json"
    manifest.write_text(json.dumps(export.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    _say(f"HOME model export complete: {main_glb.name}")
    return export


def _prefer_exact_form_textures(all_textures: dict[str, Path], parsed: HomePokemonId) -> dict[str, Path]:
    """Collapse sheets by part name, preferring the exact pm####_##_## form."""
    form_prefix = parsed.canonical.casefold()

    def part_key(name: str) -> str:
        return re.sub(r"^pm\d{4}_\d{2}_\d{2}_", "", name)

    kept: dict[str, tuple[bool, str, Path]] = {}
    for key, path in all_textures.items():
        part = part_key(key)
        exact = key.startswith(form_prefix)
        current = kept.get(part)
        if current is None or (exact and not current[0]):
            kept[part] = (exact, key, path)
    return {key: path for _, key, path in kept.values()}


def _merge_texture_sets(*, base: list[Path], override: list[Path]) -> list[Path]:
    """Overlay shiny textures on the normal set, keyed by the exact sheet name.

    Keys keep the full part token (BodyA vs BodyB vs Eye), so multi-sheet species
    swap each sheet for its `_rare` variant instead of collapsing to one texture.
    """
    def part_key(path: Path) -> str:
        stem = path.stem.casefold()
        stem = re.sub(r"^pm\d{4}_\d{2}_\d{2}_", "", stem)
        stem = re.sub(r"_(rare|shiny)$", "", stem)
        stem = re.sub(r"_col(?=_|$)", "_col", stem)
        return stem

    merged: dict[str, Path] = {}
    for path in base:
        merged.setdefault(part_key(path), path)
    for path in override:
        merged[part_key(path)] = path
    return list(merged.values())


def _build_textured_glb(source_glb: Path, out_glb: Path, textures: list[Path]) -> None:
    """Bind per-material textures then embed them into a portable single-file GLB."""
    from ...glb_policy.preview_textures import parse_glb_mesh_part_labels

    from .home_textures import align_home_bindings_to_glb, build_home_texture_bindings

    labels = parse_glb_mesh_part_labels(source_glb)
    mesh_name = ""
    glb = read_glb(source_glb)
    for mesh in glb.json.get("meshes") or []:
        if isinstance(mesh, dict) and str(mesh.get("name") or "").strip():
            mesh_name = str(mesh["name"]).strip()
            break

    bindings = build_home_texture_bindings(mesh_name, textures, part_names=labels)
    bindings = align_home_bindings_to_glb(source_glb, bindings, texture_paths=textures)

    texture_by_name = {p.stem.casefold(): p for p in textures}
    texture_by_name.update({k: v for k, v in bindings.texture_by_name.items()})

    write_patched_preview_glb(
        source_glb,
        out_glb,
        mesh_labels=labels,
        mesh_texture_paths=[None] * len(labels),
        texture_by_name=texture_by_name,
        material_to_texture=bindings.material_to_texture,
        stage_texture_paths=textures,
    )
    # Fold the external PNG URIs into the GLB binary so the file is self-contained.
    embedded = embed_glb_external_images(
        read_glb(out_glb),
        base_dir=out_glb.parent,
        search_paths=[p.parent for p in textures] + [out_glb.parent],
    )
    embedded.write(out_glb)


def _export_animation_clip_dumps(
    cache_dirs: list[Path],
    parsed: HomePokemonId,
    out_root: Path,
    *,
    progress=None,
) -> list[Path]:
    """Dump every readable AnimationClip for the species as text (typetree) files."""
    if resolve_assetstudio_cli() is None:
        return []
    anim_dir = out_root / "animations"
    dumped: list[Path] = []
    for cache_dir in cache_dirs:
        args = [
            str(cache_dir),
            "-m", "dump",
            "--load-all",
            "--filter-by-name", f"pm{parsed.number:04d}",
            "-o", str(anim_dir),
        ]
        try:
            _run_assetstudio(args, allow_failure=True)
        except Exception:
            continue
    if anim_dir.is_dir():
        # Keep only the animation-clip dumps (they live under .../fbx/ac/).
        for path in sorted(anim_dir.rglob("*.txt")):
            if "/ac/" in path.as_posix():
                target = anim_dir / path.name
                if path != target:
                    shutil.move(str(path), target)
                dumped.append(target)
        # Remove non-clip dumps and now-empty folders.
        for path in list(anim_dir.rglob("*.txt")):
            if path.parent != anim_dir:
                path.unlink(missing_ok=True)
        for folder in sorted((p for p in anim_dir.rglob("*") if p.is_dir()), reverse=True):
            try:
                folder.rmdir()
            except OSError:
                pass
        if not dumped:
            shutil.rmtree(anim_dir, ignore_errors=True)
    return dumped


def _export_animation_fbx(
    cache_dirs: list[Path],
    parsed: HomePokemonId,
    out_root: Path,
    *,
    progress=None,
) -> Path | None:
    """Best-effort: export an FBX bound with any readable animations for the species."""
    if resolve_assetstudio_cli() is None:
        return None
    anim_dir = out_root / "_anim_tmp"
    anim_dir.mkdir(parents=True, exist_ok=True)
    for cache_dir in cache_dirs:
        args = [
            str(cache_dir),
            "-m", "animator",
            "--filter-by-name", f"pm{parsed.number:04d}",
            "--fbx-animation", "auto",
            "-o", str(anim_dir),
        ]
        try:
            _run_assetstudio(args, allow_failure=True)
        except Exception:
            continue
    fbx_files = sorted(anim_dir.rglob("*.fbx"), key=lambda p: p.stat().st_size, reverse=True)
    if not fbx_files:
        shutil.rmtree(anim_dir, ignore_errors=True)
        return None
    target = out_root / f"{parsed.canonical}_animations.fbx"
    shutil.copy2(fbx_files[0], target)
    shutil.rmtree(anim_dir, ignore_errors=True)
    return target
