"""Models Resource-ready LBX DAE packages and resumable batch export."""
from __future__ import annotations

import io
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import tempfile
from typing import Callable, Iterable
from xml.etree import ElementTree as ET
import zipfile

from PIL import Image

from ...install import project_root
from .cgfx_preview import PreparedCgfxModel, prepare_cgfx_model
from .gltf.glb_io import read_glb
from .lbx_catalog import LbxCatalog, LbxExportJob


SnapshotRenderer = Callable[[Path, LbxExportJob], bytes | None]
Progress = Callable[[str], None]


_WEAPON_PREVIEW_X_ROTATION_DEG = -90.0
_UPRIGHT_WEAPON_PREVIEW_X_ROTATION_DEG = 90.0
_UPRIGHT_WEAPON_PREVIEW_Z_ROTATION_DEG = -90.0


def is_lbx_upright_weapon(job: LbxExportJob) -> bool:
    """Return whether an LBX weapon uses the front-facing upright profile."""
    stem = PurePosixPath(job.romfs_path).stem.casefold()
    return stem.startswith("wpn_sh_") or stem.startswith("wpn_fi_fi")


def descriptor_for_path(catalog: LbxCatalog, romfs_path: str) -> dict:
    entry = next((item for item in catalog.entries if item.path == romfs_path), None)
    if entry is None:
        raise FileNotFoundError(f"{romfs_path} is not present in {catalog.rom_path.name}")
    return {
        "type": "cgfx_model",
        "game": "lbx",
        "rom": str(catalog.rom_path),
        "romfs_path": romfs_path,
        "offset": entry.offset,
        "size": entry.size,
        "name": PurePosixPath(romfs_path).stem,
    }


def _frame(image: Image.Image, size: tuple[int, int], inset: tuple[int, int]) -> Image.Image:
    image = image.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds is None:
        raise RuntimeError("rendered LBX preview is fully transparent")
    subject = image.crop(bounds)
    max_width = max(1, size[0] - inset[0] * 2)
    max_height = max(1, size[1] - inset[1] * 2)
    scale = min(max_width / subject.width, max_height / subject.height)
    subject = subject.resize(
        (max(1, round(subject.width * scale)), max(1, round(subject.height * scale))),
        Image.Resampling.LANCZOS if scale < 1 else Image.Resampling.BICUBIC,
    )
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.alpha_composite(
        subject,
        ((size[0] - subject.width) // 2, (size[1] - subject.height) // 2),
    )
    return canvas


def _write_zip(path: Path, sources: Iterable[Path]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        unique = {source.name.casefold(): source for source in sources}
        for source in sorted(unique.values(), key=lambda item: item.name.casefold()):
            info = zipfile.ZipInfo(source.name, date_time=(2000, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, source.read_bytes())


def _copy_prepared_to_archive(
    prepared: PreparedCgfxModel,
    archive_dir: Path,
    dae_name: str,
    *,
    texture_variant: str = "variant",
) -> list[Path]:
    dae_target = archive_dir / dae_name
    texture_renames: dict[str, str] = {}
    written: list[Path] = []
    for texture in prepared.texture_pngs:
        target = archive_dir / texture.name
        if target.exists() and target.read_bytes() != texture.read_bytes():
            token = re.sub(r"[^a-z0-9]+", "_", texture_variant.casefold()).strip("_")
            candidate = archive_dir / f"{texture.stem}_{token}{texture.suffix}"
            counter = 2
            while candidate.exists() and candidate.read_bytes() != texture.read_bytes():
                candidate = archive_dir / f"{texture.stem}_{token}_{counter}{texture.suffix}"
                counter += 1
            target = candidate
            texture_renames[texture.name] = target.name
        if not target.exists():
            shutil.copy2(texture, target)
            written.append(target)
    shutil.copy2(prepared.dae_path, dae_target)
    if texture_renames:
        tree = ET.parse(dae_target)
        for node in tree.findall(".//{*}image/{*}init_from"):
            source_name = PurePosixPath(str(node.text or "").replace("\\", "/")).name
            if source_name in texture_renames:
                node.text = texture_renames[source_name]
        tree.write(dae_target, encoding="utf-8", xml_declaration=True)
    written.insert(0, dae_target)
    return written


def write_lbx_preview_glb(
    source: Path,
    destination: Path,
    *,
    x_rotation_deg: float = 0.0,
    z_rotation_deg: float = 0.0,
) -> None:
    """Write a preview-only GLB with an optional long-axis rotation.

    The source DAE remains untouched.  Wrapping each glTF scene preserves meshes,
    skins, and animation targets while changing how the model is presented by the
    Models Resource viewer.
    """
    x_rotation = math.fmod(float(x_rotation_deg), 360.0)
    z_rotation = math.fmod(float(z_rotation_deg), 360.0)
    if (
        math.isclose(x_rotation, 0.0, abs_tol=1e-7)
        and math.isclose(z_rotation, 0.0, abs_tol=1e-7)
    ):
        shutil.copy2(source, destination)
        return

    glb = read_glb(source)
    document = glb.json
    nodes = list(document.get("nodes") or [])
    wrapped = False
    for scene_index, scene in enumerate(document.get("scenes") or []):
        roots = list(scene.get("nodes") or [])
        if not roots:
            continue
        for axis, degrees in (("X", x_rotation), ("Z", z_rotation)):
            if math.isclose(degrees, 0.0, abs_tol=1e-7):
                continue
            half_angle = math.radians(degrees) / 2.0
            sine, cosine = math.sin(half_angle), math.cos(half_angle)
            rotation = [sine, 0.0, 0.0, cosine] if axis == "X" else [0.0, 0.0, sine, cosine]
            wrapper_index = len(nodes)
            nodes.append({
                "name": f"RAE Preview Orientation {axis} {scene_index + 1}",
                "rotation": rotation,
                "children": roots,
            })
            roots = [wrapper_index]
        scene["nodes"] = roots
        wrapped = True
    if not wrapped:
        raise RuntimeError("preview GLB contains no scene roots to orient")
    document["nodes"] = nodes
    glb.write(destination)


def _glb_document(path: Path) -> dict:
    payload = path.read_bytes()
    if len(payload) < 20:
        raise RuntimeError("preview GLB is truncated")
    magic, version, declared_size = struct.unpack_from("<4sII", payload)
    json_size, kind = struct.unpack_from("<I4s", payload, 12)
    if magic != b"glTF" or version != 2 or declared_size != len(payload) or kind != b"JSON":
        raise RuntimeError("preview GLB has an invalid glTF 2.0 header")
    try:
        document = json.loads(payload[20:20 + json_size].rstrip(b" \0"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise RuntimeError(f"preview GLB JSON is invalid: {exc}") from exc
    if not isinstance(document, dict):
        raise RuntimeError("preview GLB JSON is not an object")
    return document


def validate_lbx_submission(
    package: Path,
    icon: Path,
    glb: Path,
    preview: Path,
) -> None:
    with zipfile.ZipFile(package) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("submission ZIP failed its CRC check")
        names = archive.namelist()
        dae_names = [name for name in names if name.casefold().endswith(".dae")]
        if not dae_names:
            raise RuntimeError("submission ZIP contains no DAE model")
        if any(PurePosixPath(name).suffix.casefold() in {".bcmdl", ".bctex", ".bcskla"} for name in names):
            raise RuntimeError("submission ZIP contains a forbidden raw game asset")
        for dae_name in dae_names:
            try:
                root = ET.fromstring(archive.read(dae_name))
            except ET.ParseError as exc:
                raise RuntimeError(f"{dae_name} is not valid COLLADA XML: {exc}") from exc
            if not root.findall(".//{*}geometry"):
                raise RuntimeError(f"{dae_name} contains no geometry")
            refs = {
                PurePosixPath(str(node.text or "").replace("\\", "/")).name
                for node in root.findall(".//{*}image/{*}init_from")
                if str(node.text or "").strip()
            }
            if not refs.issubset({PurePosixPath(name).name for name in names}):
                missing = sorted(refs - {PurePosixPath(name).name for name in names})
                raise RuntimeError(f"{dae_name} references missing texture(s): {', '.join(missing)}")

    for image_path, expected in ((icon, (148, 125)), (preview, (750, 650))):
        with Image.open(image_path) as image:
            if image.format != "PNG" or image.mode != "RGBA" or image.size != expected:
                raise RuntimeError(f"{image_path.name} has an invalid PNG format or dimensions")
            low, high = image.getchannel("A").getextrema()
            if low != 0 or high == 0:
                raise RuntimeError(f"{image_path.name} does not have a transparent background")

    document = _glb_document(glb)
    if not document.get("meshes"):
        raise RuntimeError("preview GLB contains no model mesh")
    if any("bufferView" not in image for image in document.get("images") or []):
        raise RuntimeError("preview GLB contains an external texture")


def submission_complete(directory: Path, title: str) -> bool:
    paths = (
        directory / f"{title}.zip",
        directory / f"{title}_icon.png",
        directory / f"{title}_preview.glb",
        directory / f"{title}_preview.png",
    )
    try:
        validate_lbx_submission(*paths)
        return True
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile):
        return False


def _lbx_output_root(output_root: Path) -> Path:
    root = Path(output_root)
    return root if root.name.casefold() == "lbx" else root / "LBX"


def export_lbx_submission(
    catalog: LbxCatalog,
    job: LbxExportJob,
    output_root: Path,
    snapshot_renderer: SnapshotRenderer,
    *,
    title_override: str | None = None,
    progress: Progress | None = None,
) -> list[Path]:
    title = title_override or job.title
    target = _lbx_output_root(output_root) / Path(*job.category) / title
    target.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda _message: None)
    cache_root = project_root() / "exports" / "threeds_cgfx_previews"
    with tempfile.TemporaryDirectory(prefix="rae_lbx_submission_") as temp_name:
        work = Path(temp_name)
        archive_dir = work / "archive"
        archive_dir.mkdir()
        prepared = prepare_cgfx_model(
            descriptor_for_path(catalog, job.romfs_path), cache_root, progress=report
        )
        archive_files = _copy_prepared_to_archive(prepared, archive_dir, f"{title}.dae")
        preview_prepared = prepared
        if job.custom_r_path:
            custom_r = prepare_cgfx_model(
                descriptor_for_path(catalog, job.custom_r_path), cache_root, progress=report
            )
            archive_files.extend(
                _copy_prepared_to_archive(
                    custom_r,
                    archive_dir,
                    f"{title} Custom R.dae",
                    texture_variant="custom_r",
                )
            )
            preview_prepared = custom_r
        for index, auxiliary_path in enumerate(job.auxiliary_paths, 1):
            auxiliary = prepare_cgfx_model(
                descriptor_for_path(catalog, auxiliary_path), cache_root, progress=report
            )
            suffix = " Transparency" if len(job.auxiliary_paths) == 1 else f" Transparency {index}"
            archive_files.extend(
                _copy_prepared_to_archive(
                    auxiliary,
                    archive_dir,
                    f"{title}{suffix}.dae",
                    texture_variant=f"transparency_{index}",
                )
            )

        presentation_glb = work / "presentation.glb"
        if is_lbx_upright_weapon(job):
            preview_x_rotation = _UPRIGHT_WEAPON_PREVIEW_X_ROTATION_DEG
            preview_z_rotation = _UPRIGHT_WEAPON_PREVIEW_Z_ROTATION_DEG
        elif job.category and job.category[0].casefold() == "weapons":
            preview_x_rotation = _WEAPON_PREVIEW_X_ROTATION_DEG
            preview_z_rotation = 0.0
        else:
            preview_x_rotation = 0.0
            preview_z_rotation = 0.0
        write_lbx_preview_glb(
            preview_prepared.glb_path,
            presentation_glb,
            x_rotation_deg=preview_x_rotation,
            z_rotation_deg=preview_z_rotation,
        )

        report(f"Rendering Models Resource previews for {title}…")
        snapshot = snapshot_renderer(presentation_glb, job)
        if not snapshot:
            raise RuntimeError("Three.js did not produce an LBX submission preview")
        rendered = Image.open(io.BytesIO(snapshot)).convert("RGBA")
        icon_image = _frame(rendered, (148, 125), (5, 5))
        preview_image = _frame(rendered, (750, 650), (25, 25))

        package = work / f"{title}.zip"
        icon = work / f"{title}_icon.png"
        glb = work / f"{title}_preview.glb"
        preview = work / f"{title}_preview.png"
        _write_zip(package, archive_files)
        icon_image.save(icon, format="PNG", optimize=True)
        shutil.copy2(presentation_glb, glb)
        preview_image.save(preview, format="PNG", optimize=True)
        validate_lbx_submission(package, icon, glb, preview)

        final = [target / path.name for path in (package, icon, glb, preview)]
        for source, destination in zip((package, icon, glb, preview), final):
            shutil.copy2(source, destination)
        return final


def export_lbx_jobs(
    catalog: LbxCatalog,
    jobs: Iterable[LbxExportJob],
    output_root: Path,
    snapshot_renderer: SnapshotRenderer,
    *,
    force: bool = False,
    force_job: Callable[[LbxExportJob], bool] | None = None,
    progress: Progress | None = None,
) -> dict:
    jobs = list(jobs)
    root = _lbx_output_root(output_root)
    root.mkdir(parents=True, exist_ok=True)
    report: dict = {"total": len(jobs), "exported": [], "skipped": [], "errors": []}
    report_path = root / "lbx_models_resource_export_report.json"
    for position, job in enumerate(jobs, 1):
        target = root / Path(*job.category) / job.title
        label = "/".join((*job.category, job.title))
        rebuild = force or (force_job(job) if force_job is not None else False)
        if not rebuild and submission_complete(target, job.title):
            report["skipped"].append({"path": job.romfs_path, "title": job.title})
            if progress:
                progress(f"[{position}/{len(jobs)}] Already complete: {label}")
        else:
            if progress:
                progress(f"[{position}/{len(jobs)}] Exporting {label}")
            try:
                export_lbx_submission(
                    catalog, job, output_root, snapshot_renderer, progress=progress
                )
                report["exported"].append({
                    "path": job.romfs_path,
                    "title": job.title,
                    "category": list(job.category),
                    "aliases": list(job.aliases),
                    "custom_r": job.custom_r_path,
                    "auxiliary": list(job.auxiliary_paths),
                })
            except Exception as exc:
                report["errors"].append({
                    "path": job.romfs_path, "title": job.title, "error": str(exc)
                })
                if progress:
                    progress(f"Failed {label}: {exc}")
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
