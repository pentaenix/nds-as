"""Lazy extraction and conversion services for Marine Park Empire models."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import struct
from typing import Callable

import numpy as np
from PIL import Image

from .am import (
    Am1Mesh, AnimationSet, MatrixKey, Skeleton, animation_clips_in_export_order,
    decode_am1, decode_am2, decode_am3,
)
from .animated_dae import write_animated_dae
from .animated_gltf import write_animated_glb
from .container import (
    InstallShieldEntry,
    WindowsIsoContainerError,
    extract_installshield_member,
    find_unshield,
)
from .exporters import write_dae, write_glb
from .smo import SmoDecodeError, SmoMesh, decode_smo


Progress = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class PreparedModel:
    source_model: Path
    mesh: SmoMesh | Am1Mesh
    texture_pngs: tuple[Path | None, ...]
    glb_path: Path
    dae_path: Path | None
    warnings: tuple[str, ...]
    animation_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PreparedPreview:
    glb_path: Path
    warnings: tuple[str, ...]


_PREVIEW_CACHE_SCHEMA = 7


def _safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return name or "model"


def _descriptor_record(descriptor: dict, name: str) -> dict:
    value = descriptor.get(name)
    if not isinstance(value, dict) or not value.get("path"):
        raise ValueError(f"Windows ISO descriptor has no {name} record")
    return value


def _catalog(cache_dir: Path) -> list[InstallShieldEntry]:
    path = cache_dir / "installshield-catalog.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
        return [
            InstallShieldEntry(path=str(row["path"]), size=int(row["size"]))
            for row in rows
        ]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise WindowsIsoContainerError(f"InstallShield catalog cache is unreadable: {exc}") from exc


def _texture_entry(
    texture_name: str,
    model_path: str,
    entries: list[InstallShieldEntry],
) -> InstallShieldEntry | None:
    target = PurePosixPath(texture_name).stem.casefold()
    model_category = PurePosixPath(model_path).parent.name.casefold()
    candidates = [
        entry
        for entry in entries
        if entry.extension in {".dds", ".tga", ".bmp", ".png"}
        and PurePosixPath(entry.path).stem.casefold() == target
    ]
    if not candidates:
        return None

    def score(entry: InstallShieldEntry) -> tuple[int, int, str]:
        parts = [part.casefold() for part in PurePosixPath(entry.path).parts]
        value = 0
        if model_category and model_category in parts:
            value += 100
        if "model" in parts:
            value += 50
        if "preview" in parts:
            value -= 100
        if entry.extension == ".dds":
            value += 10
        return value, entry.size, entry.path.casefold()

    return max(candidates, key=score)


def _convert_texture(source: Path, output: Path) -> Path:
    output.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        image.convert("RGBA").save(output, format="PNG", optimize=True)
    return output


def _dependency_record(descriptor: dict, model_path: str, format_name: str) -> dict:
    rows = [row for row in descriptor.get("animations", [])
            if isinstance(row, dict) and str(row.get("format", "")).upper() == format_name]
    if not rows:
        raise ValueError(f"animated model has no associated {format_name} record")
    target_stem = PurePosixPath(model_path).stem.casefold()
    target_parent = PurePosixPath(model_path).parent.as_posix().casefold()

    def score(row: dict) -> tuple[int, int, str]:
        path = str(row.get("path") or "")
        source = PurePosixPath(path)
        value = 0
        if source.stem.casefold() == target_stem:
            value += 200
        if source.parent.as_posix().casefold() == target_parent:
            value += 100
        if format_name == "AM2" and source.parts and source.parts[0].casefold() == "anim":
            value += 50
        if format_name == "AM3" and source.parts and source.parts[0].casefold() == "model":
            value += 50
        return value, int(row.get("size") or 0), path.casefold()

    return max(rows, key=score)


def _animation_records(descriptor: dict, model_path: str) -> list[dict]:
    rows = [row for row in descriptor.get("animations", [])
            if isinstance(row, dict) and str(row.get("format", "")).upper() == "AM2"]
    if not rows:
        raise ValueError("animated model has no associated AM2 record")
    target_stem = PurePosixPath(model_path).stem.casefold()
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(PurePosixPath(str(row.get("path") or "")).stem.casefold(), []).append(row)

    def score(row: dict) -> tuple[int, int, int, str]:
        path = str(row.get("path") or "")
        source = PurePosixPath(path)
        return (100 if source.parts and source.parts[0].casefold() == "anim" else 0,
                50 if source.stem.casefold() == target_stem else 0,
                int(row.get("size") or 0), path.casefold())

    return [max(group, key=score) for _, group in sorted(grouped.items())]


def _fit_animation_bones(animation: AnimationSet, bone_count: int) -> AnimationSet | None:
    """Apply the source game's known one-bone AM1/AM2 compatibility quirk."""
    difference = bone_count - len(animation.tracks)
    if difference == 0:
        return animation
    if difference == -1:
        return AnimationSet(animation.actions, animation.duration_ms, animation.tracks[:bone_count])
    if difference == 1:
        identity = np.eye(4, dtype=np.float64)
        static_track = (
            MatrixKey(0.0, identity.copy()),
            MatrixKey(max(0.0, animation.duration_ms), identity.copy()),
        )
        return AnimationSet(animation.actions, animation.duration_ms, (*animation.tracks, static_track))
    return None


def prepare_model(
    descriptor: dict,
    output_dir: Path,
    *,
    include_dae: bool = True,
    animation_scope: str = "all",
    animation_clip_limit: int | None = None,
    dae_human_t_pose: bool = False,
    progress: Progress | None = None,
) -> PreparedModel:
    """Extract and convert one descriptor without exposing raw files in outputs."""
    model = _descriptor_record(descriptor, "model")
    model_path = str(model["path"])
    model_format = str(model.get("format") or "").upper()
    if model_format not in {"SMO", "AM1"}:
        raise NotImplementedError(f"unsupported Marine Park Empire model format {model_format}")
    cache_dir = Path(str(descriptor.get("cache_dir") or ""))
    if not cache_dir.is_dir():
        raise WindowsIsoContainerError("Windows ISO cabinet cache is unavailable; rescan the disc")
    unshield = find_unshield()
    if not unshield:
        raise WindowsIsoContainerError("unshield was not found; install it or set RAE_UNSHIELD")
    if progress:
        progress(f"Extracting {PurePosixPath(model_path).name}…")
    source_model = extract_installshield_member(cache_dir, model_path, unshield)
    skeleton: Skeleton | None = None
    animations: list[AnimationSet] = []
    decode_warnings: list[str] = []
    if model_format == "SMO":
        mesh = decode_smo(source_model.read_bytes())
    else:
        mesh = decode_am1(source_model.read_bytes())
        am3 = _dependency_record(descriptor, model_path, "AM3")
        am2_records = _animation_records(descriptor, model_path)
        if animation_scope == "primary":
            am2_records = am2_records[:1]
        elif animation_scope != "all":
            raise ValueError(f"unsupported animation scope: {animation_scope}")
        if progress:
            progress(f"Extracting skeleton and animation tracks for {PurePosixPath(model_path).name}…")
        skeleton = decode_am3(
            extract_installshield_member(cache_dir, str(am3["path"]), unshield).read_bytes()
        )
        for am2 in am2_records:
            decoded = decode_am2(
                extract_installshield_member(cache_dir, str(am2["path"]), unshield).read_bytes()
            )
            fitted = _fit_animation_bones(decoded, mesh.bone_count)
            if fitted is not None:
                animations.append(fitted)
                if len(decoded.tracks) != mesh.bone_count:
                    decode_warnings.append(
                        f"adapted {am2['path']} from {len(decoded.tracks)} to "
                        f"{mesh.bone_count} bones (source game one-bone rig variant)"
                    )
            else:
                decode_warnings.append(
                    f"skipped {am2['path']}: {len(decoded.tracks)} animation bones, "
                    f"model has {mesh.bone_count}"
                )
        if not animations:
            raise ValueError("none of the associated AM2 animation sets match this model's skeleton")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    base = _safe_name(PurePosixPath(model_path).stem)
    entries = _catalog(cache_dir)
    texture_pngs: list[Path | None] = []
    warnings = list(mesh.warnings) if isinstance(mesh, SmoMesh) else []
    warnings.extend(decode_warnings)
    for slot, texture_name in enumerate(mesh.texture_names):
        entry = _texture_entry(texture_name, model_path, entries)
        if entry is None:
            warnings.append(f"texture not found: {texture_name}")
            texture_pngs.append(None)
            continue
        if progress:
            progress(f"Decoding texture {slot + 1}/{len(mesh.texture_names)}: {entry.path}")
        source_texture = extract_installshield_member(cache_dir, entry.path, unshield)
        texture_out = output_dir / f"{_safe_name(PurePosixPath(texture_name).stem)}.png"
        try:
            texture_pngs.append(_convert_texture(source_texture, texture_out))
        except Exception as exc:
            warnings.append(f"texture decode failed for {entry.path}: {exc}")
            texture_pngs.append(None)

    if progress:
        progress("Writing self-contained GLB preview…")
    if isinstance(mesh, SmoMesh):
        glb_path = write_glb(
            mesh, output_dir / f"{base}_preview.glb", texture_pngs=texture_pngs,
        )
    else:
        assert skeleton is not None and animations
        glb_path = write_animated_glb(
            mesh, skeleton, animations, output_dir / f"{base}_preview.glb",
            texture_pngs=texture_pngs, max_clips=animation_clip_limit,
        )
    dae_path: Path | None = None
    if include_dae:
        if progress:
            progress("Writing COLLADA model…")
        filenames = [path.name if path else None for path in texture_pngs]
        if isinstance(mesh, SmoMesh):
            dae_path = write_dae(mesh, output_dir / f"{base}.dae", texture_filenames=filenames)
        else:
            assert skeleton is not None and animations
            dae_path = write_animated_dae(
                mesh, skeleton, animations, output_dir / f"{base}.dae",
                texture_filenames=filenames,
                human_t_pose=dae_human_t_pose,
            )
    return PreparedModel(
        source_model=source_model,
        mesh=mesh,
        texture_pngs=tuple(texture_pngs),
        glb_path=glb_path,
        dae_path=dae_path,
        warnings=tuple(warnings),
        animation_names=tuple(
            name for _, name, _, _ in animation_clips_in_export_order(animations)
        ),
    )


def _preview_fingerprint(descriptor: dict, *, full_animations: bool) -> str:
    payload = json.dumps(
        {
            "schema": _PREVIEW_CACHE_SCHEMA,
            "full_animations": full_animations,
            "descriptor": descriptor,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _valid_glb(path: Path) -> bool:
    try:
        header = path.read_bytes()[:12]
    except OSError:
        return False
    if len(header) != 12:
        return False
    magic, version, size = struct.unpack("<4sII", header)
    return magic == b"glTF" and version == 2 and size == path.stat().st_size


def prepare_preview(
    descriptor: dict,
    output_dir: Path,
    *,
    full_animations: bool = False,
    progress: Progress | None = None,
) -> PreparedPreview:
    """Build or reuse a responsive interactive preview.

    Fast UI previews include four clips from the primary linked animation set.
    The explicit full mode and every export include all linked AM2 sets.
    """
    model = _descriptor_record(descriptor, "model")
    base = _safe_name(PurePosixPath(str(model["path"])).stem)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    glb_path = output_dir / f"{base}_preview.glb"
    manifest_path = output_dir / ".rae-preview-cache.json"
    fingerprint = _preview_fingerprint(descriptor, full_animations=full_animations)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        manifest = {}
    if manifest.get("fingerprint") == fingerprint and _valid_glb(glb_path):
        if progress:
            progress(f"Using cached Windows ISO preview: {glb_path.name}")
        warnings = manifest.get("warnings")
        return PreparedPreview(
            glb_path,
            tuple(str(item) for item in warnings) if isinstance(warnings, list) else (),
        )

    result = prepare_model(
        descriptor,
        output_dir,
        include_dae=False,
        animation_scope="all" if full_animations else "primary",
        animation_clip_limit=None if full_animations else 4,
        progress=progress,
    )
    manifest_path.write_text(
        json.dumps(
            {"fingerprint": fingerprint, "warnings": list(result.warnings)},
            indent=2,
            ensure_ascii=False,
        ) + "\n",
        encoding="utf-8",
    )
    return PreparedPreview(result.glb_path, result.warnings)
