"""UI mixin module — delegates export logic to platform modules via PlatformDispatch."""
from __future__ import annotations

import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox, QDialog, QVBoxLayout, QLabel, QRadioButton, QDialogButtonBox

from ...core.modules import PlatformDispatch
from ...exporter import apicula_available, apicula_help_text, archive_directory_as_zip, export_asset, export_assets, export_readable_asset
from ...scanner import Asset


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

    def _rom_platform(self) -> str | None:
        return getattr(self, "_rom_platform_id", None)

    def _folder_export_options(self) -> list[tuple[str, str, str]]:
        return PlatformDispatch.folder_export_options(rom_platform_id=self._rom_platform())

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
        if PlatformDispatch.requires_apicula_for_folder_mode(choice, rom_platform_id=self._rom_platform()) and not apicula_available():
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
                    written_files += PlatformDispatch.export_folder_asset(
                        self, asset, mode, staging, rom_platform_id=self._rom_platform()
                    )
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

    def _export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        return PlatformDispatch.export_options_for(asset, rom_platform_id=self._rom_platform())

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
        return PlatformDispatch.run_export_choice(
            self, asset, choice, out, rom_platform_id=self._rom_platform()
        )

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
        ok, msg = PlatformDispatch.export_blender_bundle(
            self, asset, Path(out_dir), rom_platform_id=self._rom_platform()
        )
        if ok:
            QMessageBox.information(self, "Bundle exported", msg)
            self._update_status(msg)
        else:
            QMessageBox.warning(self, "Bundle conversion failed", msg)
            self._update_status("Bundle export failed.")

    def _cached_preview_texture_paths(self, out_dir: Path) -> list[Path]:
        tex_dir = out_dir / "dsm_decoded_textures"
        if not tex_dir.exists():
            return []
        return sorted([p for p in tex_dir.rglob("*.png") if p.is_file()])[:32]
