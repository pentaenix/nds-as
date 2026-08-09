"""Render Models Resource submission icons from exported building GLBs."""
from __future__ import annotations

import io
import json
import math
import tempfile
import zipfile
from pathlib import Path
from typing import Callable

from PIL import Image

from ....easyfind.model_thumbnail import render_glb_orthographic_thumbnail
from ..gltf.glb_io import read_glb

_SOURCE_ICON_SIZE = (192, 162)
_FINAL_ICON_SIZE = (148, 125)
_LARGE_PREVIEW_SIZE = (750, 650)
SnapshotRenderer = Callable[[Path], bytes | None]


def _frame_front_render(image: Image.Image) -> Image.Image:
    """Tightly frame visible pixels and ground-align them on the final canvas."""
    image = image.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise RuntimeError("rendered icon is fully transparent")
    subject = image.crop(bounds)
    scale = min(138 / max(1, subject.width), 115 / max(1, subject.height))
    size = (
        max(1, round(subject.width * scale)),
        max(1, round(subject.height * scale)),
    )
    resampling = Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.NEAREST
    subject = subject.resize(size, resampling)
    icon = Image.new("RGBA", _FINAL_ICON_SIZE, (0, 0, 0, 0))
    icon.alpha_composite(subject, ((148 - subject.width) // 2, 123 - subject.height))
    return icon


def _frame_large_preview_render(image: Image.Image) -> Image.Image:
    """Frame a full model on the required transparent 750x650 preview canvas."""
    image = image.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise RuntimeError("rendered preview is fully transparent")
    subject = image.crop(bounds)
    scale = min(700 / max(1, subject.width), 600 / max(1, subject.height))
    size = (
        max(1, round(subject.width * scale)),
        max(1, round(subject.height * scale)),
    )
    resampling = Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BICUBIC
    subject = subject.resize(size, resampling)
    preview = Image.new("RGBA", _LARGE_PREVIEW_SIZE, (0, 0, 0, 0))
    preview.alpha_composite(
        subject,
        ((_LARGE_PREVIEW_SIZE[0] - subject.width) // 2, 638 - subject.height),
    )
    return preview


def _write_front_view_model(source: Path, output: Path) -> Path:
    """Rotate a temporary render copy so original +Y is up and −Z faces camera."""
    glb = read_glb(source)
    scenes = glb.json.setdefault("scenes", [{"nodes": []}])
    scene_index = int(glb.json.get("scene") or 0)
    if not (0 <= scene_index < len(scenes)):
        scene_index = 0
        glb.json["scene"] = 0
    roots = [int(value) for value in scenes[scene_index].get("nodes") or []]
    nodes = glb.json.setdefault("nodes", [])
    nodes.append({
        "name": "rae_submission_icon_front",
        "rotation": [-math.sqrt(0.5), 0.0, 0.0, math.sqrt(0.5)],
        "children": roots,
    })
    scenes[scene_index]["nodes"] = [len(nodes) - 1]
    glb.write(output)
    return output


def _icon_name_for_package(package_name: str) -> str:
    stem = Path(package_name).stem
    if stem.endswith("_asset"):
        stem = stem[:-6]
    return f"{stem}_icon.png"


def _render_visible_icon(source: Path, package: Path) -> Image.Image:
    """Render a default pose, then a visible material-motion frame for effects."""
    from .tile_bundle import _write_motion_preview_model

    candidates: list[Path] = [source]
    with tempfile.TemporaryDirectory(prefix="rae_building_icon_") as temp_name:
        temp_root = Path(temp_name)
        for frame in (7, 24, 3, 1, 30):
            baked = temp_root / f"frame_{frame}.glb"
            if _write_motion_preview_model(source, baked, frame):
                candidates.append(baked)
        for candidate in candidates:
            front_model = temp_root / f"front_{candidate.stem}.glb"
            _write_front_view_model(candidate, front_model)
            png, width, height = render_glb_orthographic_thumbnail(
                front_model,
                width=_SOURCE_ICON_SIZE[0],
                height=_SOURCE_ICON_SIZE[1],
                yaw_deg=0.0,
                pitch_deg=0.0,
                zoom_factor=0.84,
            )
            if not png or (width, height) != _SOURCE_ICON_SIZE:
                continue
            image = Image.open(io.BytesIO(png)).convert("RGBA").transpose(
                Image.Transpose.FLIP_TOP_BOTTOM
            )
            if image.getchannel("A").getbbox() is not None:
                return _frame_front_render(image)

    # Some effect models are hidden in their bind pose and become visible only
    # through a skeletal clip. A faithful source texture is the clearest static
    # icon for those planes, and the site explicitly permits shader replication.
    with zipfile.ZipFile(package) as archive:
        textures: list[Image.Image] = []
        for name in archive.namelist():
            if not name.casefold().endswith(".png"):
                continue
            texture = Image.open(io.BytesIO(archive.read(name))).convert("RGBA")
            if texture.getchannel("A").getbbox() is not None:
                textures.append(texture)
        if not textures:
            raise RuntimeError("rendered icon is fully transparent")
        texture = max(
            textures,
            key=lambda item: sum(1 for value in item.getchannel("A").getdata() if value),
        )
        scale = min(112 / max(1, texture.width), 100 / max(1, texture.height))
        texture = texture.resize(
            (max(1, round(texture.width * scale)), max(1, round(texture.height * scale))),
            Image.Resampling.NEAREST,
        )
        icon = Image.new("RGBA", (148, 125), (0, 0, 0, 0))
        icon.alpha_composite(texture, ((148 - texture.width) // 2, (125 - texture.height) // 2))
        return icon


def _render_visible_web_icon(
    source: Path,
    package: Path,
    snapshot_renderer: SnapshotRenderer,
) -> Image.Image:
    """Render a front view through RAE's EasyFind Three.js preview pipeline."""
    from .tile_bundle import _write_motion_preview_model

    candidates: list[Path] = [source]
    with tempfile.TemporaryDirectory(prefix="rae_building_web_icon_") as temp_name:
        temp_root = Path(temp_name)
        for frame in (7, 24, 3, 1, 30):
            baked = temp_root / f"frame_{frame}.glb"
            if _write_motion_preview_model(source, baked, frame):
                candidates.append(baked)
        for candidate in candidates:
            png = snapshot_renderer(candidate)
            if not png:
                continue
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            if image.getchannel("A").getbbox() is not None:
                return _frame_front_render(image)

    # Preserve the existing faithful source-texture fallback for effect planes
    # that become visible only after a skeletal clip starts playing.
    with zipfile.ZipFile(package) as archive:
        textures: list[Image.Image] = []
        for name in archive.namelist():
            if not name.casefold().endswith(".png"):
                continue
            texture = Image.open(io.BytesIO(archive.read(name))).convert("RGBA")
            if texture.getchannel("A").getbbox() is not None:
                textures.append(texture)
        if not textures:
            raise RuntimeError("rendered icon is fully transparent")
        texture = max(
            textures,
            key=lambda item: sum(1 for value in item.getchannel("A").getdata() if value),
        )
        scale = min(112 / max(1, texture.width), 100 / max(1, texture.height))
        texture = texture.resize(
            (max(1, round(texture.width * scale)), max(1, round(texture.height * scale))),
            Image.Resampling.NEAREST,
        )
        icon = Image.new("RGBA", _FINAL_ICON_SIZE, (0, 0, 0, 0))
        icon.alpha_composite(texture, ((148 - texture.width) // 2, (125 - texture.height) // 2))
        return icon


def _render_visible_web_preview(
    source: Path,
    package: Path,
    snapshot_renderer: SnapshotRenderer,
) -> Image.Image:
    """Render a large preview through the same EasyFind Three.js pipeline."""
    from .tile_bundle import _write_motion_preview_model

    candidates: list[Path] = [source]
    with tempfile.TemporaryDirectory(prefix="rae_building_web_preview_") as temp_name:
        temp_root = Path(temp_name)
        for frame in (7, 24, 3, 1, 30):
            baked = temp_root / f"frame_{frame}.glb"
            if _write_motion_preview_model(source, baked, frame):
                candidates.append(baked)
        for candidate in candidates:
            png = snapshot_renderer(candidate)
            if not png:
                continue
            image = Image.open(io.BytesIO(png)).convert("RGBA")
            if image.getchannel("A").getbbox() is not None:
                return _frame_large_preview_render(image)

    with zipfile.ZipFile(package) as archive:
        textures: list[Image.Image] = []
        for name in archive.namelist():
            if not name.casefold().endswith(".png"):
                continue
            texture = Image.open(io.BytesIO(archive.read(name))).convert("RGBA")
            if texture.getchannel("A").getbbox() is not None:
                textures.append(texture)
        if not textures:
            raise RuntimeError("rendered preview is fully transparent")
        texture = max(
            textures,
            key=lambda item: sum(1 for value in item.getchannel("A").getdata() if value),
        )
        scale = min(700 / max(1, texture.width), 600 / max(1, texture.height))
        texture = texture.resize(
            (max(1, round(texture.width * scale)), max(1, round(texture.height * scale))),
            Image.Resampling.NEAREST,
        )
        preview = Image.new("RGBA", _LARGE_PREVIEW_SIZE, (0, 0, 0, 0))
        preview.alpha_composite(
            texture,
            ((_LARGE_PREVIEW_SIZE[0] - texture.width) // 2, 638 - texture.height),
        )
        return preview


def render_models_resource_building_icon(
    source: Path,
    package: Path,
    snapshot_renderer: SnapshotRenderer,
) -> Image.Image:
    """Render one submission icon with the Three.js snapshot pipeline."""
    return _render_visible_web_icon(
        Path(source),
        Path(package),
        snapshot_renderer,
    )


def render_models_resource_building_preview_icon(
    source: Path,
    package: Path,
    snapshot_renderer: SnapshotRenderer,
) -> Image.Image:
    """Render one transparent 750x650 Models Resource preview icon."""
    return _render_visible_web_preview(
        Path(source),
        Path(package),
        snapshot_renderer,
    )


def render_models_resource_building_icons(
    dae_export_dir: Path,
    glb_export_dir: Path,
    *,
    progress: Callable[[str], None] | None = None,
    force: bool = False,
    snapshot_renderer: SnapshotRenderer | None = None,
) -> dict[str, object]:
    """Render transparent 148x125 PNGs beside their matching asset ZIPs."""
    dae_export_dir = Path(dae_export_dir)
    glb_export_dir = Path(glb_export_dir)
    dae_catalog_path = dae_export_dir / "submission_catalog.json"
    glb_catalog_path = glb_export_dir / "building_catalog.json"
    dae_catalog = json.loads(dae_catalog_path.read_text(encoding="utf-8"))
    glb_catalog = json.loads(glb_catalog_path.read_text(encoding="utf-8"))
    glb_by_hash = {
        str(item["sha256"]): glb_export_dir / str(item["file"])
        for item in glb_catalog.get("files") or []
    }
    rows = list(dae_catalog.get("files") or [])
    errors: list[dict[str, str]] = []
    written = 0
    for index, row in enumerate(rows, 1):
        package = dae_export_dir / str(row["package"])
        icon = package.parent / _icon_name_for_package(package.name)
        source = glb_by_hash.get(str(row["sha256"]))
        if progress:
            progress(f"Rendering icon {index}/{len(rows)}: {row['sourceModelName']}")
        try:
            if source is None or not source.is_file():
                raise FileNotFoundError("matching GLB source is unavailable")
            if force or not icon.is_file():
                if snapshot_renderer is None:
                    image = _render_visible_icon(source, package)
                else:
                    image = _render_visible_web_icon(source, package, snapshot_renderer)
                image.save(icon, format="PNG", optimize=True)
            row["icon"] = f"submission_zips/{icon.name}"
            written += 1
        except Exception as exc:
            errors.append({
                "package": str(row.get("package") or ""),
                "error": str(exc),
            })
    dae_catalog["icons"] = {
        "format": "PNG",
        "width": 148,
        "height": 125,
        "transparentBackground": True,
        "renderer": "three.js" if snapshot_renderer is not None else "cpu",
        "camera": "three-quarter" if snapshot_renderer is not None else "legacy-front",
        "rendered": written,
        "errors": errors,
    }
    dae_catalog_path.write_text(json.dumps(dae_catalog, indent=2) + "\n", encoding="utf-8")
    return dae_catalog["icons"]
