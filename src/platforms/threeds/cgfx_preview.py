"""Lazy extraction and GLB preview generation for named CGFX model assets."""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable
from xml.etree import ElementTree as ET

from ...install import project_root
from .cgfx_policy import apply_cgfx_dae_policy, apply_cgfx_glb_policy
from .cgfx_tool import CgfxToolError, run_cgfx_bridge
from .container import RomFsFile, ThreedsImage
from .gltf.embed_textures import embed_glb_external_images
from .gltf.glb_io import read_glb


def _conversion_metadata(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True, slots=True)
class PreparedCgfxModel:
    glb_path: Path
    dae_path: Path
    texture_pngs: tuple[Path, ...]
    animation_names: tuple[str, ...]
    conversion: dict


def _identity(stem: str, *, game_id: str) -> str:
    parts = stem.casefold().split("_")
    if game_id == "lbx":
        if stem.casefold().startswith(("lbx", "chr")) and len(parts) >= 2:
            return "_".join(parts[:2])
        return stem.casefold()
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return stem.casefold()


def _related_entries(descriptor: dict) -> tuple[list[RomFsFile], list[RomFsFile]]:
    rom = descriptor["rom"]
    source_path = str(descriptor["romfs_path"])
    source_stem = PurePosixPath(source_path).stem.casefold()
    source_parent = str(PurePosixPath(source_path).parent)
    identity = _identity(source_stem, game_id=str(descriptor.get("game") or "generic"))
    with ThreedsImage(rom) as image:
        entries = image.romfs_files()
    textures: list[RomFsFile] = []
    animations: list[RomFsFile] = []
    for entry in entries:
        path = entry.path.casefold()
        stem = PurePosixPath(path).stem
        if path.endswith(".bctex") and str(PurePosixPath(entry.path).parent) == source_parent:
            if stem.startswith(identity) or source_stem.startswith(stem):
                textures.append(entry)
        elif path.endswith(".bcskla") and stem.startswith(identity + "_"):
            animations.append(entry)
    animations.sort(key=lambda entry: ("idle" not in entry.path.casefold(), entry.path.casefold()))
    return textures[:16], animations[:1]


def _extract_entries(rom: str, entries: list[RomFsFile], output: Path) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    with ThreedsImage(rom) as image:
        for index, entry in enumerate(entries):
            name = f"{index:03d}_{PurePosixPath(entry.path).name}"
            path = output / name
            path.write_bytes(image.read(entry.offset, entry.size))
            written.append(path)
    return written


def _dae_texture_paths(dae_path: Path) -> tuple[Path, ...]:
    try:
        root = ET.parse(dae_path).getroot()
    except (OSError, ET.ParseError):
        return ()
    paths: list[Path] = []
    for node in root.findall(".//{*}image/{*}init_from"):
        name = str(node.text or "").strip().replace("\\", "/")
        if not name:
            continue
        path = dae_path.parent / PurePosixPath(name).name
        if path.is_file() and path not in paths:
            paths.append(path)
    return tuple(paths)


def _ensure_material_metadata(
    conversion: dict,
    model_path: Path,
    conversion_path: Path,
    *,
    progress: Callable[[str], None],
) -> dict:
    if isinstance(conversion.get("materials"), list):
        return conversion
    metadata = run_cgfx_bridge(["inspect", model_path], progress=progress)
    try:
        inspected = json.loads(metadata)
    except (TypeError, ValueError):
        inspected = {}
    if isinstance(inspected, dict) and isinstance(inspected.get("materials"), list):
        conversion = {**conversion, "materials": inspected["materials"]}
        conversion_path.write_text(json.dumps(conversion, separators=(",", ":")), encoding="utf-8")
    return conversion


def prepare_cgfx_model(
    descriptor: dict,
    output_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> PreparedCgfxModel:
    """Prepare the reusable DAE, textures, and policy-correct self-contained GLB."""
    report = progress or (lambda _message: None)
    asset_name = str(descriptor.get("name") or "cgfx_model")
    asset_key = PurePosixPath(str(descriptor["romfs_path"])).stem
    offset = int(descriptor.get("offset") or 0)
    cache = Path(output_dir) / f"{asset_key}_{offset:x}"
    glb_path = cache / f"{asset_name}.glb"
    dae_path = cache / f"{asset_name}.dae"
    conversion_path = cache / "conversion.json"
    cache.mkdir(parents=True, exist_ok=True)
    model_path = cache / PurePosixPath(str(descriptor["romfs_path"])).name
    if not model_path.is_file():
        with ThreedsImage(descriptor["rom"]) as image:
            source = next(
                (entry for entry in image.romfs_files() if entry.path == descriptor["romfs_path"]),
                None,
            )
            if source is None:
                raise FileNotFoundError(descriptor["romfs_path"])
            model_path.write_bytes(image.read(source.offset, source.size))

    conversion = _conversion_metadata(conversion_path)
    if not glb_path.is_file() or not dae_path.is_file():
        textures, animations = _related_entries(descriptor)
        companions = _extract_entries(
            descriptor["rom"], [*textures, *animations], cache / "companions"
        )
        report(
            f"3DS CGFX: converting {asset_name} with {len(textures)} texture and "
            f"{len(animations)} animation companion(s)…"
        )
        metadata = run_cgfx_bridge(["export", dae_path, model_path, *companions], progress=report)
        if metadata:
            conversion_path.write_text(metadata, encoding="utf-8")
            conversion = _conversion_metadata(conversion_path)
        assimp = shutil.which("assimp")
        if assimp is None:
            raise CgfxToolError(
                "CGFX preview conversion requires Assimp (`brew install assimp` on macOS)."
            )
        result = subprocess.run(
            [assimp, "export", str(dae_path), str(glb_path), "-f", "glb2"],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode or not glb_path.is_file():
            raise CgfxToolError((result.stderr or result.stdout or "Assimp conversion failed").strip())
    conversion = _ensure_material_metadata(
        conversion, model_path, conversion_path, progress=report
    )
    apply_cgfx_dae_policy(
        dae_path,
        game_id=str(descriptor.get("game") or "generic"),
        material_states=conversion.get("materials"),
    )
    texture_pngs = _dae_texture_paths(dae_path)
    embedded = embed_glb_external_images(
        read_glb(glb_path),
        base_dir=glb_path.parent,
        search_paths=list(texture_pngs),
        require_all=True,
    )
    embedded.write(glb_path)
    selected_animation = conversion.get("selectedAnimation")
    apply_cgfx_glb_policy(
        glb_path,
        game_id=str(descriptor.get("game") or "generic"),
        animation_name=selected_animation if isinstance(selected_animation, str) else None,
        material_states=conversion.get("materials"),
    )
    manifest = {
        "source": descriptor["romfs_path"],
        "textures": [path.name for path in texture_pngs],
        "selectedAnimation": selected_animation,
    }
    (cache / "rae_cgfx_preview.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report(f"3DS CGFX model preview ready: {glb_path.name}")
    animation_names = (selected_animation,) if isinstance(selected_animation, str) else ()
    return PreparedCgfxModel(
        glb_path=glb_path,
        dae_path=dae_path,
        texture_pngs=texture_pngs,
        animation_names=animation_names,
        conversion=conversion,
    )


def build_cgfx_model_glb(
    descriptor: dict,
    output_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    return prepare_cgfx_model(descriptor, output_dir, progress=progress).glb_path
