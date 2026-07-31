"""Lazy extraction and GLB preview generation for named CGFX model assets."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Callable

from ...install import project_root
from .cgfx_policy import apply_cgfx_glb_policy
from .cgfx_tool import CgfxToolError, run_cgfx_bridge
from .container import RomFsFile, ThreedsImage


def _conversion_metadata(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _identity(stem: str) -> str:
    parts = stem.casefold().split("_")
    if len(parts) >= 2:
        return "_".join(parts[:2])
    return stem.casefold()


def _related_entries(descriptor: dict) -> tuple[list[RomFsFile], list[RomFsFile]]:
    rom = descriptor["rom"]
    source_path = str(descriptor["romfs_path"])
    source_stem = PurePosixPath(source_path).stem.casefold()
    source_parent = str(PurePosixPath(source_path).parent)
    identity = _identity(source_stem)
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


def build_cgfx_model_glb(
    descriptor: dict,
    output_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
) -> Path:
    report = progress or (lambda _message: None)
    asset_name = str(descriptor.get("name") or "cgfx_model")
    asset_key = PurePosixPath(str(descriptor["romfs_path"])).stem
    offset = int(descriptor.get("offset") or 0)
    cache = Path(output_dir) / f"{asset_key}_{offset:x}"
    glb_path = cache / f"{asset_name}.glb"
    if glb_path.is_file():
        conversion = _conversion_metadata(cache / "conversion.json")
        selected_animation = conversion.get("selectedAnimation")
        apply_cgfx_glb_policy(
            glb_path,
            game_id=str(descriptor.get("game") or "generic"),
            animation_name=selected_animation if isinstance(selected_animation, str) else None,
        )
        report(f"3DS CGFX: using cached preview {glb_path.name}")
        return glb_path
    cache.mkdir(parents=True, exist_ok=True)
    with ThreedsImage(descriptor["rom"]) as image:
        source = next(
            (entry for entry in image.romfs_files() if entry.path == descriptor["romfs_path"]),
            None,
        )
        if source is None:
            raise FileNotFoundError(descriptor["romfs_path"])
        model_path = cache / PurePosixPath(source.path).name
        model_path.write_bytes(image.read(source.offset, source.size))
    textures, animations = _related_entries(descriptor)
    companions = _extract_entries(descriptor["rom"], [*textures, *animations], cache / "companions")
    report(
        f"3DS CGFX: converting {asset_name} with {len(textures)} texture and "
        f"{len(animations)} animation companion(s)…"
    )
    dae_path = cache / f"{asset_name}.dae"
    metadata = run_cgfx_bridge(["export", dae_path, model_path, *companions], progress=report)
    conversion: dict = {}
    if metadata:
        conversion_path = cache / "conversion.json"
        conversion_path.write_text(metadata, encoding="utf-8")
        conversion = _conversion_metadata(conversion_path)
    assimp = shutil.which("assimp")
    if assimp is None:
        raise CgfxToolError("CGFX preview conversion requires Assimp (`brew install assimp` on macOS).")
    result = subprocess.run(
        [assimp, "export", str(dae_path), str(glb_path), "-f", "glb2"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode or not glb_path.is_file():
        raise CgfxToolError((result.stderr or result.stdout or "Assimp conversion failed").strip())
    selected_animation = conversion.get("selectedAnimation")
    apply_cgfx_glb_policy(
        glb_path,
        game_id=str(descriptor.get("game") or "generic"),
        animation_name=selected_animation if isinstance(selected_animation, str) else None,
    )
    manifest = {
        "source": descriptor["romfs_path"],
        "textures": [entry.path for entry in textures],
        "animations": [entry.path for entry in animations],
    }
    (cache / "rae_cgfx_preview.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    report(f"3DS CGFX model preview ready: {glb_path.name}")
    return glb_path
