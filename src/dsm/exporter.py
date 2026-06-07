from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

Progress = Callable[[str], None]

from .scanner import Asset
from .util import sanitize_virtual_path


@dataclass(slots=True)
class ConvertResult:
    ok: bool
    message: str
    output_files: list[Path]
    command: list[str]
    auxiliary_files: list[Path] = field(default_factory=list)


def export_asset(asset: Asset, out_dir: str | Path, *, decoded: bool = True) -> Path:
    base = Path(out_dir)
    rel = sanitize_virtual_path(asset.virtual_path)
    if rel.suffix == "":
        rel = rel.with_suffix(asset.extension)
    out_path = base / rel
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(asset.data if decoded else asset.original_data)
    return out_path


def export_assets(assets: Iterable[Asset], out_dir: str | Path, *, decoded: bool = True) -> list[Path]:
    return [export_asset(asset, out_dir, decoded=decoded) for asset in assets]


def archive_directory_as_zip(source_dir: str | Path, zip_path: str | Path) -> int:
    """Write every file under source_dir into zip_path. Returns file count."""
    root = Path(source_dir)
    target = Path(zip_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            zf.write(path, path.relative_to(root).as_posix())
            count += 1
    return count


def find_apicula() -> str | None:
    """Return the best apicula executable path DSM can find.

    apicula is a Rust CLI, not a pip-installable Python package. DSM checks PATH,
    DSM_APICULA, and the common in-repo Cargo build location used by the README:
    tools/apicula/target/release/apicula.
    """
    names = ["apicula"]
    if os.name == "nt":
        names.insert(0, "apicula.exe")

    env_path = (os.environ.get("DSAS_APICULA") or os.environ.get("DSM_APICULA") or "").strip()
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())

    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    roots: list[Path] = [Path.cwd()]
    here = Path(__file__).resolve()
    roots.extend(here.parents)
    for root in roots:
        for name in names:
            candidates.append(root / "tools" / "apicula" / "target" / "release" / name)

    seen: set[Path] = set()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            resolved = candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.exists() and resolved.is_file():
            return str(resolved)
    return None


def apicula_available() -> bool:
    return find_apicula() is not None


def apicula_help_text() -> str:
    return (
        "apicula was not found. NDS-AS looks on PATH, DSAS_APICULA, and "
        "tools/apicula/target/release/apicula.\n\n"
        "From the nds-as repo root, build it with:\n\n"
        "brew install rust\n"
        "cd tools/apicula && cargo build --release\n\n"
        "Then restart NDS-AS."
    )


def converted_outputs(out_dir: str | Path, output_format: str = "glb") -> list[Path]:
    out_path = Path(out_dir)
    if not out_path.exists():
        return []
    patterns = [f"*.{output_format}", "*.glb", "*.gltf", "*.dae"]
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(out_path.rglob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                found.append(path)
    return found



def convert_texture_with_apicula(
    asset: Asset,
    out_dir: str | Path,
    *,
    more_textures: bool = True,
) -> ConvertResult:
    """Use apicula to extract/render images from a BTX0/NSBTX texture file.

    For Pokémon/Game Freak map assets, the model and texture archive may be split.
    Converting the BTX0 separately is useful even when apicula cannot attach the
    texture to a model, because it still gives you the PNG texture candidates to
    inspect or apply manually in Blender.
    """
    apicula = find_apicula()
    if not apicula:
        return ConvertResult(False, apicula_help_text(), [], [])
    if asset.magic != "BTX0":
        return ConvertResult(False, "Selected asset is not a BTX0/NSBTX texture file.", [], [])

    out_path = Path(out_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="dsm_tex_") as tmp:
        tmp_path = Path(tmp)
        tex_path = _write_temp_asset(asset, tmp_path, prefix="texture")
        cmd = [apicula, "convert", "--overwrite"]
        if more_textures:
            cmd.append("--more-textures")
        cmd.extend([str(tex_path), "-o", str(out_path)])
        proc = subprocess.run(cmd, text=True, capture_output=True)
        outputs = texture_outputs(out_path)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "apicula texture convert failed").strip()
            return ConvertResult(False, msg, outputs, cmd)
        if not outputs:
            # Some apicula builds may emit DAE/GLTF wrappers, so include generic outputs too.
            outputs = converted_outputs(out_path)
        if not outputs:
            return ConvertResult(False, "apicula completed, but no texture/image output was found", outputs, cmd)
        return ConvertResult(True, "Texture extraction completed", outputs, cmd)


def texture_outputs(out_dir: str | Path) -> list[Path]:
    out_path = Path(out_dir)
    if not out_path.exists():
        return []
    patterns = ["*.png", "*.bmp", "*.tga", "*.jpg", "*.jpeg"]
    found: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(out_path.rglob(pattern)):
            if path.is_file() and path not in seen:
                seen.add(path)
                found.append(path)
    return found

def convert_with_apicula(
    asset: Asset,
    out_dir: str | Path,
    *,
    sibling_assets: Iterable[Asset] = (),
    output_format: str = "glb",
    more_textures: bool = True,
) -> ConvertResult:
    """Export selected asset plus relevant siblings, then call apicula convert.

    apicula can often use a model plus sibling BTX0 texture/animation files when all are
    supplied together. We write temp files with Nitro extensions so the converter has
    useful names to work with.

    When more_textures=True, DSM also passes apicula's --more-textures flag. This
    asks apicula to export extra texture/palette combinations from supplied BTX0
    files, which helps Game Freak/Pokémon-style layouts where map textures are not
    embedded in the BMD0 model.
    """
    apicula = find_apicula()
    if not apicula:
        return ConvertResult(False, apicula_help_text(), [], [])

    out_path = Path(out_dir)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="dsm_") as tmp:
        tmp_path = Path(tmp)
        input_files: list[Path] = []

        selected_path = _write_temp_asset(asset, tmp_path, prefix="selected")
        input_files.append(selected_path)

        for i, sibling in enumerate(sibling_assets):
            if sibling.asset_id == asset.asset_id:
                continue
            # Textures and animations near the model are useful. Other models are not.
            if sibling.magic not in {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}:
                continue
            input_files.append(_write_temp_asset(sibling, tmp_path, prefix=f"sibling_{i:03d}"))

        cmd = [apicula, "convert", "--overwrite"]
        if more_textures:
            cmd.append("--more-textures")
        cmd.extend([f"-f={output_format}", *map(str, input_files), "-o", str(out_path)])
        proc = subprocess.run(cmd, text=True, capture_output=True)
        outputs = converted_outputs(out_path, output_format)
        if proc.returncode != 0:
            msg = (proc.stderr or proc.stdout or "apicula convert failed").strip()
            return ConvertResult(False, msg, outputs, cmd)
        if not outputs:
            return ConvertResult(False, "apicula completed, but no converted file was found", outputs, cmd)
        return ConvertResult(True, "Converted successfully", outputs, cmd)


def _write_temp_asset(asset: Asset, tmp_path: Path, *, prefix: str) -> Path:
    rel = sanitize_virtual_path(asset.virtual_path)
    filename = rel.name or asset.suggested_filename
    if not filename.lower().endswith(asset.extension):
        filename += asset.extension

    # Keep filenames short, extension-correct, and shell-safe. The internal Nitro
    # dictionaries carry the real texture/material names; the outer filename only
    # needs to be recognizable to apicula and easy for users to read.
    stem = Path(filename).stem[-48:] or asset.magic.lower()
    suffix = asset.extension
    out = tmp_path / f"{prefix}_{asset.asset_id}_{stem}{suffix}"
    out.write_bytes(asset.data)
    return out


def export_readable_asset(asset: Asset, out_dir: str | Path) -> list[Path]:
    """Export a human-readable PNG view when DSM knows how to decode the asset.

    Raw export is still handled by export_asset(); this is for BTX0 textures,
    NCGR/NCLR/NSCR previews, and actual PNG files found in archives.
    """
    from .nitro_textures import decode_btx_images, save_decoded_images
    from .nitro_2d import decode_nitro2d_preview, save_preview_images

    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    if asset.magic == "BTX0":
        images = decode_btx_images(asset.data, mode="all-palettes")
        return save_decoded_images(images, base / asset.asset_id, prefix=Path(asset.virtual_path).stem)
    if asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
        from .audio import export_audio_bundle
        return export_audio_bundle(asset, base)
    if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
        images = decode_nitro2d_preview(asset.data, asset.magic)
        return save_preview_images(images, base / asset.asset_id, prefix=Path(asset.virtual_path).stem)
    if asset.magic == "PNG" or asset.extension == ".png" or asset.data.startswith(b"\x89PNG"):
        rel = sanitize_virtual_path(asset.virtual_path)
        if rel.suffix.lower() != ".png":
            rel = rel.with_suffix(".png")
        out_path = base / rel
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(asset.data)
        return [out_path]
    return []
