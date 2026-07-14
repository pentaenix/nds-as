"""NDS export implementations (apicula, nitro 2D, audio bundles)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Protocol

from ....core.assets import Asset
from ....core.util import sanitize_virtual_path
from ....preview_policy import ModelPreviewQuality, model_preview_policy
from ..exporter import (
    apicula_available,
    apicula_help_text,
    convert_texture_with_apicula,
    convert_with_apicula,
    export_asset,
    export_assets,
    export_readable_asset,
    texture_outputs,
)
from ..model_module.preview_pipeline import export_textured_model_glb
from ..nitro_2d import decode_nitro2d_related_preview, save_preview_images
from ..texture_library import TextureLibrary
from .tile_bundle import export_tile_bundle


class ExportHost(Protocol):
    def _update_status(self, text: str) -> None: ...

    def _sibling_assets(self, asset: Asset) -> list[Asset]: ...

    def _pinned_texture_asset(self) -> Asset | None: ...

    def _quick_related_2d_assets(self, asset: Asset, *, limit: int = 32) -> list[Asset]: ...

    def _pokemon_path_texture_candidates(self, asset: Asset, *, limit: int = 64) -> list[Asset]: ...

    @property
    def preview_temp(self) -> Path: ...


FOLDER_EXPORT_OPTIONS: list[tuple[str, str, str]] = [
    ("folder_readable", "ZIP: Readable PNGs / previews", "Decode textures, tiles, palettes, and PNGs into a portable archive."),
    ("folder_raw", "ZIP: Raw original files", "Write the extracted Nitro payloads using their virtual ROM paths."),
    ("folder_glb", "ZIP: GLB models (BMD0 only)", "Convert every visible model in the folder to GLB via apicula."),
    ("folder_mixed", "ZIP: Mixed smart bundle", "Raw files plus readable previews and GLB models where RAE can produce them."),
]

APICULA_FOLDER_MODES = frozenset({"folder_glb", "folder_mixed"})


def export_options_for(asset: Asset) -> list[tuple[str, str, str]]:
    options: list[tuple[str, str, str]] = [
        ("raw", "Original / raw asset", "Save exactly this selected asset as RAE extracted it."),
    ]
    if asset.magic == "BMD0":
        options.extend([
            ("model_glb", "Model: GLB (self-contained)", "Convert to a single GLB with embedded textures and same-folder animation siblings via apicula."),
            ("tile_bundle", "Tile: Pokemon Resort (.tile)", "Package this model, its materials, and detected texture frames for direct tile-pack import."),
            ("model_dae", "Model: DAE / Collada via apicula", "Useful for Blender import and debugging material names."),
            ("model_obj", "Model: OBJ + MTL via GLB bridge", "Experimental: converts GLB output to OBJ/MTL using trimesh."),
            ("model_bundle", "Model: full research bundle", "Raw model, related BTX0/animations, decoded texture PNGs, GLB, DAE, reports."),
        ])
    elif asset.magic == "BTX0":
        options.extend([
            ("readable", "Texture PNGs/contact sheet", "Decode NSBTX/BTX0 textures to PNG when RAE supports the format."),
            ("texture_apicula", "Texture extraction via apicula fallback", "Try apicula's texture extraction for unusual BTX0 cases."),
        ])
    elif asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "PNG"}:
        options.extend([
            ("readable", "Readable PNG preview", "Export RAE's direct preview/contact sheet for this asset."),
            ("related_png", "Combined PNG using related tiles/palettes/cells", "Pair same-folder NCGR/NCLR/NSCR/NCER/NANR assets and compose the best preview RAE can."),
        ])
    elif asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
        options.extend([
            ("audio_bundle", "Audio bundle: raw + WAV previews", "Best-quality practical output: raw original pieces plus lossless WAV previews where RAE can decode samples/streams."),
            ("audio_bundle_mp3", "Audio bundle + optional MP3", "Also writes high-quality MP3 copies when ffmpeg is installed. WAV remains the quality-first output."),
            ("audio_open", "Create WAV preview and open it", "Exports to a preview folder and opens the first WAV with your OS default player."),
        ])
    else:
        options.append(("readable", "Try readable decode", "Try RAE's readable exporter if this format has a decoder."))
    return options


def _host_all_assets(host: ExportHost, asset: Asset) -> list[Asset]:
    assets = getattr(host, "assets", None)
    if assets:
        return list(assets)
    return [asset]


def _host_texture_library(host: ExportHost) -> TextureLibrary | None:
    getter = getattr(host, "_texture_library_for_session", None)
    if callable(getter):
        return getter()
    return None


def _host_preview_policy(host: ExportHost):
    getter = getattr(host, "_model_preview_policy", None)
    if callable(getter):
        return getter()
    return model_preview_policy(ModelPreviewQuality.FULL_FIDELITY)


def export_viewport_matched_glb(host: ExportHost, asset: Asset, out_dir: Path) -> list[Path]:
    """GLB export using the same texture resolve + patch path as the live preview."""
    host._update_status("Exporting self-contained GLB (embedded textures + animations)…")
    stem = sanitize_virtual_path(asset.virtual_path).stem or asset.asset_id
    return export_textured_model_glb(
        asset,
        _host_all_assets(host, asset),
        out_dir,
        texture_library=_host_texture_library(host),
        manual_texture=host._pinned_texture_asset(),
        policy=_host_preview_policy(host),
        progress=host._update_status,
        glb_filename=f"{stem}.glb",
    )


def model_related_assets(host: ExportHost, asset: Asset) -> list[Asset]:
    resolver_related = host._sibling_assets(asset)
    pinned = host._pinned_texture_asset()
    out: list[Asset] = []
    seen = {asset.asset_id}
    for item in ([pinned] if pinned else []) + resolver_related:
        if item and item.asset_id not in seen and item.magic in {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}:
            seen.add(item.asset_id)
            out.append(item)
    host._update_status(f"Model export will supply {len(out)} resolved/manual texture and animation sibling(s) to apicula.")
    return out[:96]


def export_folder_asset(host: ExportHost, asset: Asset, mode: str, staging: Path) -> int:
    count = 0
    if mode in {"folder_raw", "folder_mixed"}:
        export_asset(asset, staging / "raw", decoded=True)
        count += 1
    if mode in {"folder_readable", "folder_mixed"}:
        readable = export_readable_asset(asset, staging / "readable" / asset.asset_id)
        count += len(readable)
    if mode in {"folder_glb", "folder_mixed"} and asset.magic == "BMD0":
        out_dir = staging / "glb" / Path(asset.virtual_path).stem
        written = export_viewport_matched_glb(host, asset, out_dir)
        count += len(written)
    return count


def run_export_choice(host: ExportHost, asset: Asset, choice: str, out: Path) -> list[Path]:
    if choice == "raw":
        host._update_status("Writing raw selected asset...")
        return [export_asset(asset, out, decoded=True)]
    if choice == "readable":
        host._update_status("Running RAE readable decoder...")
        return export_readable_asset(asset, out)
    if choice == "related_png":
        host._update_status("Composing PNG preview from paired 2D assets...")
        related = host._quick_related_2d_assets(asset, limit=32)
        images = decode_nitro2d_related_preview(asset, related)
        if images:
            return save_preview_images(images, out / f"dsm_related_{asset.asset_id}", prefix=Path(asset.virtual_path).stem)
        return export_readable_asset(asset, out)
    if choice == "texture_apicula":
        tex_out = out / f"dsm_texture_{asset.asset_id}"
        result = convert_texture_with_apicula(asset, tex_out)
        if not result.ok:
            raise RuntimeError(result.message)
        return result.output_files or texture_outputs(tex_out)
    if choice == "model_glb":
        model_out = out / f"dsm_model_{asset.asset_id}_glb"
        return export_viewport_matched_glb(host, asset, model_out)
    if choice == "tile_bundle":
        host._update_status("Exporting Pokemon Resort tile bundle…")
        tile_path = export_tile_bundle(
            host,
            asset,
            _host_all_assets(host, asset),
            out,
            texture_library=_host_texture_library(host),
            policy=_host_preview_policy(host),
            progress=host._update_status,
        )
        return [tile_path]
    if choice == "model_dae":
        model_out = out / f"dsm_model_{asset.asset_id}_dae"
        related = model_related_assets(host, asset)
        result = convert_with_apicula(asset, model_out, sibling_assets=related, output_format="dae")
        if not result.ok:
            raise RuntimeError(result.message)
        return result.output_files
    if choice == "model_obj":
        return export_model_obj(host, asset, out)
    if choice == "model_bundle":
        return export_model_bundle(host, asset, out)
    if choice in {"audio_bundle", "audio_bundle_mp3", "audio_open"}:
        from ....ui.main.audio import export_audio_bundle

        base = out if choice != "audio_open" else (host.preview_temp / "audio_previews")
        written = export_audio_bundle(asset, base, make_mp3=(choice == "audio_bundle_mp3"))
        if choice == "audio_open":
            wavs = [p for p in written if p.suffix.lower() == ".wav"]
            if wavs:
                open_path(host, wavs[0])
        return written
    return export_readable_asset(asset, out)


def export_model_obj(host: ExportHost, asset: Asset, out: Path) -> list[Path]:
    tmp = out / f"dsm_model_{asset.asset_id}_glb_for_obj"
    glb_files = export_viewport_matched_glb(host, asset, tmp)
    glb_path = next((path for path in glb_files if path.suffix.lower() == ".glb"), None)
    if glb_path is None:
        raise RuntimeError("Model conversion failed — no GLB was produced for OBJ export.")
    try:
        import trimesh
    except Exception as exc:
        raise RuntimeError(f"trimesh is required for OBJ bridge export: {exc}") from exc
    obj_dir = out / f"dsm_model_{asset.asset_id}_obj"
    obj_dir.mkdir(parents=True, exist_ok=True)
    scene = trimesh.load(glb_path, force="scene")
    obj_path = obj_dir / "model.obj"
    scene.export(obj_path)
    return [obj_path] + list(obj_dir.glob("*.mtl")) + list(obj_dir.glob("*.png"))


def export_model_bundle(host: ExportHost, asset: Asset, out: Path) -> list[Path]:
    base = out / f"dsm_bundle_{asset.asset_id}"
    raw_dir = base / "raw_nitro"
    glb_dir = base / "converted_glb"
    dae_dir = base / "converted_dae"
    texture_dir = base / "texture_images"
    for d in (raw_dir, glb_dir, dae_dir, texture_dir):
        d.mkdir(parents=True, exist_ok=True)
    related = model_related_assets(host, asset)
    written = [export_asset(asset, raw_dir, decoded=True)]
    written.extend(export_assets(related, raw_dir / "related", decoded=True))
    for texture_asset in [r for r in related if r.magic == "BTX0"][:96]:
        try:
            written.extend(export_readable_asset(texture_asset, texture_dir / "dsm_decoded"))
        except Exception as exc:
            host._update_status(f"Texture decode failed for {texture_asset.virtual_path}: {exc}")
    try:
        glb_written = export_viewport_matched_glb(host, asset, glb_dir)
        glb_ok = bool(glb_written)
        glb_message = f"Exported self-contained GLB with embedded textures."
    except Exception as exc:
        glb_ok = False
        glb_written = []
        glb_message = str(exc)
    dae = convert_with_apicula(asset, dae_dir, sibling_assets=related, output_format="dae")
    commands = [f"[GLB] ok={glb_ok}", glb_message, ""]
    for label, result in (("DAE", dae),):
        commands.append(f"[{label}] ok={result.ok}")
        commands.append(" ".join(str(part) for part in result.command))
        commands.append(result.message)
        commands.append("")
        written.extend(result.output_files)
    written.extend(glb_written)
    log = base / "conversion_commands.txt"
    log.write_text("\n".join(commands), encoding="utf-8")
    written.append(log)
    if not glb_ok and not dae.ok:
        raise RuntimeError(glb_message or dae.message)
    return written


def export_blender_bundle(host: ExportHost, asset: Asset, out_dir: Path) -> tuple[bool, str]:
    from ..exporter import convert_texture_with_apicula
    from ..nitro_names import texture_match_report

    if not apicula_available():
        return False, apicula_help_text()
    if asset.magic != "BMD0":
        return False, "Select a BMD0 model row first."

    base = Path(out_dir) / f"dsm_bundle_{asset.asset_id}"
    raw_dir = base / "raw_nitro"
    glb_dir = base / "converted_glb"
    dae_dir = base / "converted_dae"
    base.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    siblings = host._sibling_assets(asset)
    export_asset(asset, raw_dir, decoded=True)
    exported_siblings = export_assets(siblings, raw_dir / "related", decoded=True)
    texture_pool = [s for s in siblings if s.magic == "BTX0"]
    report = texture_match_report(asset, texture_pool or host._pokemon_path_texture_candidates(asset, limit=64))
    (base / "texture_match_report.txt").write_text(report, encoding="utf-8")

    try:
        glb_written = export_viewport_matched_glb(host, asset, glb_dir)
        glb_result_ok = bool(glb_written)
        glb_result_message = f"Exported self-contained GLB with embedded textures."
    except Exception as exc:
        glb_written = []
        glb_result_ok = False
        glb_result_message = str(exc)
    dae_result = convert_with_apicula(asset, dae_dir, sibling_assets=siblings, output_format="dae")

    texture_dir = base / "texture_images"
    dsm_decoded_count = 0
    for texture_asset in texture_pool[:64]:
        try:
            dsm_decoded_count += len(export_readable_asset(texture_asset, texture_dir / "dsm_decoded"))
        except Exception:
            pass

    texture_results = []
    for texture_asset in texture_pool[:24]:
        tex_out = texture_dir / "apicula" / texture_asset.asset_id
        texture_results.append((texture_asset, convert_texture_with_apicula(texture_asset, tex_out)))

    commands = [
        f"[DSM_DECODED_TEXTURE_PNGS] count={dsm_decoded_count}",
        "",
        f"[GLB] ok={glb_result_ok}",
        glb_result_message,
        "",
    ]
    for label, result in (("DAE", dae_result),):
        commands.append(f"[{label}] ok={result.ok}")
        commands.append(" ".join(str(part) for part in result.command))
        commands.append(result.message)
        commands.append("")
    for texture_asset, result in texture_results:
        commands.append(f"[TEXTURE {texture_asset.asset_id}] ok={result.ok} path={texture_asset.virtual_path}")
        commands.append(" ".join(str(part) for part in result.command))
        commands.append(result.message)
        commands.append("")
    (base / "apicula_commands.txt").write_text("\n".join(commands), encoding="utf-8")

    if glb_result_ok or dae_result.ok:
        msg = (
            f"Exported Blender bundle to {base}\n\n"
            f"Raw model + {len(exported_siblings)} related texture/animation file(s) are in raw_nitro/.\n"
            f"Converted GLB/DAE outputs, {dsm_decoded_count} RAE-decoded texture PNG(s), apicula texture fallbacks, and logs are inside the bundle."
        )
        return True, msg
    return False, glb_result_message or dae_result.message or "Conversion failed."


def open_path(host: ExportHost, path: Path) -> None:
    try:
        if sys.platform.startswith("darwin"):
            subprocess.Popen(["open", str(path)])
        elif os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(path)])
        host._update_status(f"Opened preview: {path}")
    except Exception as exc:
        host._update_status(f"Could not open preview automatically: {exc}")


def requires_apicula_for_folder_mode(mode: str) -> bool:
    return mode in APICULA_FOLDER_MODES
