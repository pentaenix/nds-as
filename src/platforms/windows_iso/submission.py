"""Models Resource-ready package assembly for Windows ISO models."""
from __future__ import annotations

import io
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile
from typing import Callable
import zipfile
from xml.etree import ElementTree as ET

from PIL import Image

from .service import PreparedModel, prepare_model


SnapshotRenderer = Callable[[Path], bytes | None]

_TITLE_OVERRIDES = {
    "nzs_female": "New Zealand Sea Lion Female",
    "nz_sealion_baby": "New Zealand Sea Lion Baby",
}


def submission_title(model_path: str) -> str:
    """Return a readable Models Resource title for a V3D model stem."""
    stem = PurePosixPath(model_path).stem.strip()
    override = _TITLE_OVERRIDES.get(stem.casefold())
    if override:
        return override
    suffix = ""
    if stem.casefold().endswith("_baby"):
        stem = stem[:-5]
        suffix = " Baby"
    elif stem.casefold().endswith("_female"):
        stem = stem[:-7]
        suffix = " Female"
    elif len(stem) > 1 and stem.endswith("S"):
        stem = stem[:-1]
        suffix = " Baby"
    elif len(stem) > 1 and stem.endswith("F"):
        stem = stem[:-1]
        suffix = " Female"
    stem = re.sub(r"[_-]+", " ", stem)
    stem = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", stem)
    words = " ".join(stem.split())
    title = " ".join(word[:1].upper() + word[1:].lower() for word in words.split())
    safe = re.sub(r"[\\/:*?\"<>|]+", " ", f"{title}{suffix}")
    return " ".join(safe.split()) or "Model"


def submission_output_directory(root: Path, descriptor: dict) -> Path:
    """Place game submissions together without duplicating the game folder."""
    game = str(descriptor.get("game_title") or "Marine Park Empire").strip()
    folder = re.sub(r"[\\/:*?\"<>|]+", " ", game)
    folder = " ".join(folder.split()) or "Marine Park Empire"
    root = Path(root)
    return root if root.name.casefold() == folder.casefold() else root / folder


def submission_camera(model_path: str) -> tuple[float, float, float]:
    """Choose an identifiable source-faithful camera for the submission render."""
    parts = {part.casefold() for part in PurePosixPath(model_path).parts}
    if "animal" in parts:
        return 0.0, 14.0, 0.84
    return 30.0, 27.0, 0.82


def _frame(image: Image.Image, size: tuple[int, int], inset: tuple[int, int]) -> Image.Image:
    image = image.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise RuntimeError("rendered preview is fully transparent")
    subject = image.crop(bounds)
    max_width = max(1, size[0] - inset[0] * 2)
    max_height = max(1, size[1] - inset[1] * 2)
    scale = min(max_width / subject.width, max_height / subject.height)
    scaled = subject.resize(
        (max(1, round(subject.width * scale)), max(1, round(subject.height * scale))),
        Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BICUBIC,
    )
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(
        scaled,
        ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2),
    )
    return canvas


def _write_zip(path: Path, files: list[Path]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        unique = {source.name.casefold(): source for source in files}
        for source in sorted(unique.values(), key=lambda item: item.name.casefold()):
            info = zipfile.ZipInfo(source.name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def _glb_document(path: Path) -> dict:
    payload = path.read_bytes()
    if len(payload) < 20:
        raise RuntimeError("preview GLB is truncated")
    magic, version, declared_size = struct.unpack_from("<4sII", payload)
    json_size, kind = struct.unpack_from("<I4s", payload, 12)
    if magic != b"glTF" or version != 2 or declared_size != len(payload) or kind != b"JSON":
        raise RuntimeError("preview GLB has an invalid glTF 2.0 header")
    try:
        value = json.loads(payload[20:20 + json_size].rstrip(b" \0"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"preview GLB JSON is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError("preview GLB JSON is not an object")
    return value


def _validate_submission(
    package: Path,
    icon: Path,
    glb: Path,
    preview: Path,
    *,
    model_format: str,
    animation_names: tuple[str, ...],
) -> None:
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submission ZIP failed its CRC check")
        names = archive.namelist()
        if len([name for name in names if name.casefold().endswith(".dae")]) != 1:
            raise RuntimeError("submission ZIP must contain exactly one DAE model")
        forbidden = {".am1", ".am2", ".am3", ".dds", ".smo"}
        if any(PurePosixPath(name).suffix.casefold() in forbidden for name in names):
            raise RuntimeError("submission ZIP contains a forbidden raw game asset")
        dae_name = next(name for name in names if name.casefold().endswith(".dae"))
        try:
            dae_root = ET.fromstring(archive.read(dae_name))
        except ET.ParseError as exc:
            raise RuntimeError(f"submission DAE XML is invalid: {exc}") from exc
        namespace = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
        if not dae_root.findall(".//c:geometry", namespace):
            raise RuntimeError("submission DAE contains no geometry")
        image_refs = {
            str(node.text or "").strip()
            for node in dae_root.findall(".//c:image/c:init_from", namespace)
            if str(node.text or "").strip()
        }
        if not image_refs.issubset(set(names)):
            raise RuntimeError("submission DAE references a texture absent from its ZIP")
        if model_format == "AM1":
            if not dae_root.findall(".//c:controller", namespace):
                raise RuntimeError("animated submission DAE contains no skin controller")
            clips = dae_root.findall(".//c:animation_clip", namespace)
            if len(clips) != len(animation_names):
                raise RuntimeError("submission DAE does not contain every decoded animation")

    for path, expected_size in ((icon, (148, 125)), (preview, (750, 650))):
        with Image.open(path) as image:
            if image.format != "PNG" or image.mode != "RGBA" or image.size != expected_size:
                raise RuntimeError(f"{path.name} has an invalid PNG format or dimensions")
            low, high = image.getchannel("A").getextrema()
            if low != 0 or high == 0:
                raise RuntimeError(f"{path.name} does not have a transparent background")

    document = _glb_document(glb)
    if not document.get("meshes"):
        raise RuntimeError("preview GLB contains no model mesh")
    if any("bufferView" not in image for image in document.get("images") or []):
        raise RuntimeError("preview GLB contains an external texture")
    if model_format == "AM1":
        if not document.get("skins"):
            raise RuntimeError("animated preview GLB contains no skin")
        clips = [str(item.get("name") or "") for item in document.get("animations") or []]
        if clips != list(animation_names):
            raise RuntimeError("preview GLB does not contain every decoded animation in source order")
        idle = next((name for name in animation_names if "idle" in name.casefold()), None)
        if idle and clips[0] != idle:
            raise RuntimeError("preview GLB does not present its idle animation first")


def export_submission(
    descriptor: dict,
    output_dir: Path,
    snapshot_renderer: SnapshotRenderer,
    *,
    title_override: str | None = None,
    dae_human_t_pose: bool = False,
    progress: Callable[[str], None] | None = None,
) -> list[Path]:
    """Write the four matched submission artifacts beside one another."""
    model = descriptor.get("model") or {}
    model_path = str(model.get("path") or "model")
    title = title_override or submission_title(model_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rae_windows_iso_submission_") as temp_name:
        work = Path(temp_name)
        prepared: PreparedModel = prepare_model(
            descriptor,
            work,
            include_dae=True,
            dae_human_t_pose=dae_human_t_pose,
            progress=progress,
        )
        if prepared.dae_path is None:
            raise RuntimeError("COLLADA export produced no model")
        if progress:
            progress("Rendering transparent submission previews…")
        snapshot = snapshot_renderer(prepared.glb_path)
        if not snapshot:
            raise RuntimeError("Three.js did not produce a submission preview")
        source_image = Image.open(io.BytesIO(snapshot)).convert("RGBA")
        icon = _frame(source_image, (148, 125), (5, 5))
        preview = _frame(source_image, (750, 650), (25, 25))

        archive_dir = work / "archive"
        archive_dir.mkdir()
        archive_dae = archive_dir / f"{title}.dae"
        shutil.copy2(prepared.dae_path, archive_dae)
        deliverables = work / "deliverables"
        deliverables.mkdir()
        package = deliverables / f"{title}.zip"
        icon_path = deliverables / f"{title}_icon.png"
        glb_path = deliverables / f"{title}_preview.glb"
        preview_path = deliverables / f"{title}_preview.png"
        _write_zip(
            package,
            [archive_dae, *[path for path in prepared.texture_pngs if path]],
        )
        icon.save(icon_path, format="PNG", optimize=True)
        shutil.copy2(prepared.glb_path, glb_path)
        preview.save(preview_path, format="PNG", optimize=True)
        _validate_submission(
            package,
            icon_path,
            glb_path,
            preview_path,
            model_format=str(model.get("format") or "").upper(),
            animation_names=prepared.animation_names,
        )
        final_paths = [
            output_dir / package.name,
            output_dir / icon_path.name,
            output_dir / glb_path.name,
            output_dir / preview_path.name,
        ]
        for source, target in zip(
            (package, icon_path, glb_path, preview_path), final_paths,
        ):
            shutil.copy2(source, target)
    return final_paths
