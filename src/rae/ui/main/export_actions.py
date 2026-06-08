"""UI mixin module."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QBrush
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSplitter,
    QStyle,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolBar,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from ...asset_resolver import MODEL_ANIMATION_MAGICS, build_related_assets, folder_sibling_assets, pokemon_path_texture_candidates, texture_matches_for_model
from ...exporter import (
    apicula_available,
    apicula_help_text,
    archive_directory_as_zip,
    convert_texture_with_apicula,
    convert_with_apicula,
    converted_outputs,
    export_asset,
    export_assets,
    export_readable_asset,
    texture_outputs,
)
from ...install import project_root
from ...mapping import choose_mapping, mapping_summary
from ...model_texture_resolver import resolve_model_textures, write_resolution_images
from ...nds import NDSRom
from ...nitro_2d import decode_nitro2d_preview, decode_nitro2d_related_preview, save_preview_images
from ...nitro_names import asset_browser_name, asset_filename_label, extract_nitro_names, texture_match_report
from ...nitro_textures import decode_btx_images, decode_guided_tex0_images, make_contact_sheet, parse_tex0_manifest, save_decoded_images
from ...profiles import detect_profile
from ...scanner import Asset, asset_search_text, filter_assets, filter_assets_by_types, filter_assets_indexed, scan_nds_path
from ...session import load_session_zip, save_session_zip
from ...texture_library import TextureLibrary, TextureLibraryStore
from ...util import human_size
from ..constants import (
    BROWSER_COLUMNS,
    CHROME_BUTTON_STYLE,
    DEFAULT_TYPE_FILTER_ON,
    DROPDOWN_BUTTON_STYLE,
    FILTER_CHIP_STYLE,
    FLAT_COLUMNS,
    TYPE_FILTER_GROUPS,
    TYPE_FILTER_ORDER,
    TYPE_LABELS,
)
from ..preview_btx import write_btx_preview_images
from ..preview_quality import CachedTextureResolution, TextureQuality, converted_texture_quality
from ..preview_widgets import PreviewWidget, qcolor_rgbf
from ..workers import (
    FilterWorker,
    ImagePreviewWorker,
    PreviewWorker,
    ScanWorker,
    SessionLoadWorker,
    SessionSaveWorker,
    TextureLibraryWarmupWorker,
    TextureResolveWorker,
    TextureWorker,
)

class ExportActionsMixin:
    def export_selected_smart(self) -> None:
        folder = self.selected_folder()
        if folder is not None:
            self._export_selected_folder(folder)
            return
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(
                self,
                "No selection",
                "Select an asset row, or select a folder in Mapped Tree / Raw Folders to export.",
            )
            return
        options = self._export_options_for(asset)
        choice = self._choose_export_option(asset, options)
        if not choice:
            return
        default_dir = Path.cwd() / "exports"
        default_dir.mkdir(exist_ok=True)
        out_dir = QFileDialog.getExistingDirectory(self, "Choose export folder", str(default_dir))
        if not out_dir:
            return
        out = Path(out_dir)
        self.info_tabs.setCurrentWidget(self.log_box)
        self._update_status(f"Export Selected: {choice} for {asset.virtual_path}")
        try:
            written = self._run_export_choice(asset, choice, out)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            self._update_status(f"Export failed: {exc}")
            return
        if written:
            msg = f"Exported {len(written)} file(s) to:\n{out}"
        else:
            msg = f"Export finished to:\n{out}\n\nNo readable derived files were produced, but check raw outputs/logs if applicable."
        QMessageBox.information(self, "Export complete", msg)
        self._update_status(msg.replace("\n", " "))

    def _folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            ("folder_readable", "ZIP: Readable PNGs / previews", "Decode textures, tiles, palettes, and PNGs into a portable archive."),
            ("folder_raw", "ZIP: Raw original files", "Write the extracted Nitro payloads using their virtual ROM paths."),
            ("folder_glb", "ZIP: GLB models (BMD0 only)", "Convert every visible model in the folder to GLB via apicula."),
            ("folder_mixed", "ZIP: Mixed smart bundle", "Raw files plus readable previews and GLB models where RAE can produce them."),
        ]

    def _export_selected_folder(self, folder: tuple[tuple[str, ...], bool]) -> None:
        parts, raw = folder
        assets = self._folder_assets(parts, raw)
        if not assets:
            QMessageBox.information(self, "Empty folder", "This folder has no visible assets to export.")
            return
        options = self._folder_export_options()
        choice = self._choose_export_option_dialog(
            "Export Folder",
            f"Folder: {' / '.join(parts)}\nAssets in folder: {len(assets)}",
            options,
        )
        if not choice:
            return
        if choice in {"folder_glb", "folder_mixed"} and not apicula_available():
            QMessageBox.warning(self, "apicula not found", apicula_help_text())
            return
        default_dir = Path.cwd() / "exports"
        default_dir.mkdir(exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", parts[-1] if parts else "folder").strip("_") or "folder"
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save folder export ZIP",
            str(default_dir / f"folder_{safe_name}.zip"),
            "ZIP archive (*.zip)",
        )
        if not out_path:
            return
        zip_path = Path(out_path)
        if zip_path.suffix.lower() != ".zip":
            zip_path = zip_path.with_suffix(".zip")
        self.info_tabs.setCurrentWidget(self.log_box)
        self._update_status(f"Exporting {len(assets)} asset(s) from {' / '.join(parts)} to {zip_path.name}...")
        try:
            file_count, errors = self._build_folder_zip(assets, parts, choice, zip_path)
        except Exception as exc:
            QMessageBox.warning(self, "Folder export failed", str(exc))
            self._update_status(f"Folder export failed: {exc}")
            return
        msg = f"Exported {file_count} file(s) to:\n{zip_path}"
        if errors:
            msg += f"\n\n{len(errors)} asset(s) had errors. See folder_export_log.txt inside the ZIP."
        QMessageBox.information(self, "Folder export complete", msg)
        self._update_status(msg.replace("\n", " "))

    def _build_folder_zip(
        self,
        assets: list[Asset],
        parts: tuple[str, ...],
        mode: str,
        zip_path: Path,
    ) -> tuple[int, list[str]]:
        errors: list[str] = []
        written_files = 0
        with tempfile.TemporaryDirectory(prefix="dsm_folder_export_") as tmp:
            staging = Path(tmp)
            manifest = {
                "format": "dsm-folder-export-v1",
                "folder": list(parts),
                "mode": mode,
                "asset_count": len(assets),
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
            total = len(assets)
            for index, asset in enumerate(assets, start=1):
                self._update_status(f"Folder export {index}/{total}: {asset.virtual_path}")
                try:
                    written_files += self._export_folder_asset(asset, mode, staging)
                except Exception as exc:
                    errors.append(f"{asset.virtual_path}: {exc}")
            manifest["errors"] = errors
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if errors:
                log = staging / "folder_export_log.txt"
                log.write_text("\n".join(errors), encoding="utf-8")
            written_files += 1
            file_count = archive_directory_as_zip(staging, zip_path)
        return file_count, errors

    def _export_folder_asset(self, asset: Asset, mode: str, staging: Path) -> int:
        count = 0
        if mode in {"folder_raw", "folder_mixed"}:
            export_asset(asset, staging / "raw", decoded=True)
            count += 1
        if mode in {"folder_readable", "folder_mixed"}:
            readable = export_readable_asset(asset, staging / "readable" / asset.asset_id)
            count += len(readable)
        if mode in {"folder_glb", "folder_mixed"} and asset.magic == "BMD0":
            related = self._model_related_assets(asset)[:32]
            out_dir = staging / "glb" / Path(asset.virtual_path).stem
            out_dir.mkdir(parents=True, exist_ok=True)
            result = convert_with_apicula(asset, out_dir, sibling_assets=related, output_format="glb")
            if not result.ok:
                raise RuntimeError(result.message)
            count += len(result.output_files or [])
        return count

    def _export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        options: list[tuple[str, str, str]] = [("raw", "Original / raw asset", "Save exactly this selected asset as RAE extracted it.")]
        if asset.magic == "BMD0":
            options.extend([
                ("model_glb", "Model: GLB via apicula", "Convert selected model with resolved textures and same-folder animation siblings supplied to apicula."),
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

    def _choose_export_option(self, asset: Asset, options: list[tuple[str, str, str]]) -> str | None:
        return self._choose_export_option_dialog(
            "Export Selected",
            f"Choose how to export:\n{asset.magic} — {asset.virtual_path}",
            options,
        )

    def _choose_export_option_dialog(
        self,
        title: str,
        header: str,
        options: list[tuple[str, str, str]],
    ) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        label = QLabel(header)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons: list[QRadioButton] = []
        for idx, (_key, option_label, desc) in enumerate(options):
            rb = QRadioButton(f"{option_label}\n  {desc}")
            rb.setChecked(idx == 0)
            layout.addWidget(rb)
            buttons.append(rb)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)
        if dialog.exec() != QDialog.Accepted:
            return None
        for rb, (key, _label, _desc) in zip(buttons, options):
            if rb.isChecked():
                return key
        return options[0][0] if options else None

    def _run_export_choice(self, asset: Asset, choice: str, out: Path) -> list[Path]:
        written: list[Path] = []
        if choice == "raw":
            self._update_status("Writing raw selected asset...")
            return [export_asset(asset, out, decoded=True)]
        if choice == "readable":
            self._update_status("Running RAE readable decoder...")
            return export_readable_asset(asset, out)
        if choice == "related_png":
            self._update_status("Composing PNG preview from paired 2D assets...")
            related = self._quick_related_2d_assets(asset, limit=32)
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
        if choice in {"model_glb", "model_dae"}:
            fmt = "glb" if choice == "model_glb" else "dae"
            model_out = out / f"dsm_model_{asset.asset_id}_{fmt}"
            related = self._model_related_assets(asset)
            result = convert_with_apicula(asset, model_out, sibling_assets=related, output_format=fmt)
            if not result.ok:
                raise RuntimeError(result.message)
            return result.output_files
        if choice == "model_obj":
            return self._export_model_obj(asset, out)
        if choice == "model_bundle":
            return self._export_model_bundle_to(asset, out)
        if choice in {"audio_bundle", "audio_bundle_mp3", "audio_open"}:
            from .audio import export_audio_bundle
            base = out if choice != "audio_open" else (self.preview_temp / "audio_previews")
            written = export_audio_bundle(asset, base, make_mp3=(choice == "audio_bundle_mp3"))
            if choice == "audio_open":
                wavs = [p for p in written if p.suffix.lower() == ".wav"]
                if wavs:
                    self._open_path(wavs[0])
            return written
        return export_readable_asset(asset, out)

    def _model_related_assets(self, asset: Asset) -> list[Asset]:
        resolver_related = self._sibling_assets(asset)
        pinned = self._pinned_texture_asset()
        out: list[Asset] = []
        seen = {asset.asset_id}
        for item in ([pinned] if pinned else []) + resolver_related:
            if item and item.asset_id not in seen and item.magic in {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}:
                seen.add(item.asset_id)
                out.append(item)
        self._update_status(f"Model export will supply {len(out)} resolved/manual texture and animation sibling(s) to apicula.")
        return out[:96]

    def _export_model_obj(self, asset: Asset, out: Path) -> list[Path]:
        tmp = out / f"dsm_model_{asset.asset_id}_glb_for_obj"
        related = self._model_related_assets(asset)
        result = convert_with_apicula(asset, tmp, sibling_assets=related, output_format="glb")
        if not result.ok or not result.output_files:
            raise RuntimeError(result.message)
        try:
            import trimesh
        except Exception as exc:
            raise RuntimeError(f"trimesh is required for OBJ bridge export: {exc}") from exc
        obj_dir = out / f"dsm_model_{asset.asset_id}_obj"
        obj_dir.mkdir(parents=True, exist_ok=True)
        scene = trimesh.load(result.output_files[0], force="scene")
        obj_path = obj_dir / "model.obj"
        scene.export(obj_path)
        return [obj_path] + list(obj_dir.glob("*.mtl")) + list(obj_dir.glob("*.png"))

    def _export_model_bundle_to(self, asset: Asset, out: Path) -> list[Path]:
        base = out / f"dsm_bundle_{asset.asset_id}"
        raw_dir = base / "raw_nitro"
        glb_dir = base / "converted_glb"
        dae_dir = base / "converted_dae"
        texture_dir = base / "texture_images"
        for d in (raw_dir, glb_dir, dae_dir, texture_dir):
            d.mkdir(parents=True, exist_ok=True)
        related = self._model_related_assets(asset)
        written = [export_asset(asset, raw_dir, decoded=True)]
        written.extend(export_assets(related, raw_dir / "related", decoded=True))
        # Decode resolved/manual textures directly; this is useful even if apicula does not embed them.
        for texture_asset in [r for r in related if r.magic == "BTX0"][:96]:
            try:
                written.extend(export_readable_asset(texture_asset, texture_dir / "dsm_decoded"))
            except Exception as exc:
                self._update_status(f"Texture decode failed for {texture_asset.virtual_path}: {exc}")
        glb = convert_with_apicula(asset, glb_dir, sibling_assets=related, output_format="glb")
        dae = convert_with_apicula(asset, dae_dir, sibling_assets=related, output_format="dae")
        commands = []
        for label, result in (("GLB", glb), ("DAE", dae)):
            commands.append(f"[{label}] ok={result.ok}")
            commands.append(" ".join(str(part) for part in result.command))
            commands.append(result.message)
            commands.append("")
            written.extend(result.output_files)
        log = base / "conversion_commands.txt"
        log.write_text("\n".join(commands), encoding="utf-8")
        written.append(log)
        if not glb.ok and not dae.ok:
            raise RuntimeError(glb.message or dae.message)
        return written

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self._update_status(f"Opened preview: {path}")
        except Exception as exc:
            self._update_status(f"Could not open preview automatically: {exc}")

    def export_readable_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select an asset first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose readable export folder")
        if not out_dir:
            return
        try:
            written = export_readable_asset(asset, out_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Readable export failed", str(exc))
            self._update_status("Readable export failed.")
            return
        if written:
            QMessageBox.information(self, "Readable export complete", f"Wrote {len(written)} file(s) to:\n{Path(out_dir)}")
            self._update_status(f"Wrote {len(written)} readable file(s) from {asset.virtual_path}")
        else:
            QMessageBox.information(self, "No readable decoder", "RAE does not have a readable PNG export for this asset yet. Use Export Selected to save the raw decoded file.")
            self._update_status("No readable decoder for selected asset.")

    def export_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select an asset first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not out_dir:
            return
        out_path = export_asset(asset, out_dir, decoded=True)
        self._update_status(f"Exported {asset.virtual_path} -> {out_path}")

    def export_visible(self) -> None:
        if not self.visible_assets:
            QMessageBox.information(self, "Nothing to export", "There are no visible assets to export.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not out_dir:
            return
        exported = export_assets(self.visible_assets, out_dir, decoded=True)
        self._update_status(f"Exported {len(exported)} asset(s) to {out_dir}")

    def export_blender_bundle(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BMD0 model first.")
            return
        if asset.magic != "BMD0":
            QMessageBox.information(self, "Not a model", "Select a BMD0 model row first.")
            return
        if not apicula_available():
            QMessageBox.warning(self, "apicula not found", apicula_help_text())
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose bundle export folder")
        if not out_dir:
            return

        base = Path(out_dir) / f"dsm_bundle_{asset.asset_id}"
        raw_dir = base / "raw_nitro"
        glb_dir = base / "converted_glb"
        dae_dir = base / "converted_dae"
        base.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        siblings = self._sibling_assets(asset)
        export_asset(asset, raw_dir, decoded=True)
        exported_siblings = export_assets(siblings, raw_dir / "related", decoded=True)
        texture_pool = [s for s in siblings if s.magic == "BTX0"]
        report = texture_match_report(asset, texture_pool or self._pokemon_path_texture_candidates(asset, limit=64))
        (base / "texture_match_report.txt").write_text(report, encoding="utf-8")

        glb_result = convert_with_apicula(asset, glb_dir, sibling_assets=siblings, output_format="glb")
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

        commands = [f"[DSM_DECODED_TEXTURE_PNGS] count={dsm_decoded_count}", ""]
        for label, result in (("GLB", glb_result), ("DAE", dae_result)):
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

        if glb_result.ok or dae_result.ok:
            msg = (
                f"Exported Blender bundle to {base}\n\n"
                f"Raw model + {len(exported_siblings)} related texture/animation file(s) are in raw_nitro/.\n"
                f"Converted GLB/DAE outputs, {dsm_decoded_count} RAE-decoded texture PNG(s), apicula texture fallbacks, and logs are inside the bundle."
            )
            QMessageBox.information(self, "Bundle exported", msg)
            self._update_status(msg)
        else:
            QMessageBox.warning(self, "Bundle conversion failed", glb_result.message or dae_result.message)
            self._update_status("Bundle export wrote raw files, but conversion failed. Check apicula_commands.txt.")

    def _cached_preview_texture_paths(self, out_dir: Path) -> list[Path]:
        tex_dir = out_dir / "dsm_decoded_textures"
        if not tex_dir.exists():
            return []
        return sorted([p for p in tex_dir.rglob("*.png") if p.is_file()])[:32]

