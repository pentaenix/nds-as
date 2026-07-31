from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from ....core.modules.protocols import ModelModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....core.assets import Asset
from ....core.qthread import launch_qthread
from ....install import project_root
from ..service import prepare_preview
from ..inspector import register_inspector


class WindowsIsoPreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, object)
    failed = Signal(str)

    def __init__(self, descriptor: dict, output_dir: Path, *, full_animations: bool = False):
        super().__init__()
        self.descriptor = descriptor
        self.output_dir = output_dir
        self.full_animations = full_animations

    def run(self) -> None:
        try:
            result = prepare_preview(
                self.descriptor,
                self.output_dir,
                full_animations=self.full_animations,
                progress=self.progress.emit,
            )
            self.finished_ok.emit(str(result.glb_path), list(result.warnings))
        except Exception as exc:
            self.failed.emit(str(exc))


class WindowsIsoModelModule:
    platform_id = "windows_iso"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic in {"WSMO", "WAM1"}:
            return PreviewRoute.MODEL
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        if asset.magic not in {"WSMO", "WAM1"}:
            return False
        try:
            descriptor = json.loads(asset.data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return False
        window = ctx.window
        output_dir = project_root() / "exports" / "windows_iso_previews" / asset.asset_id
        worker = WindowsIsoPreviewWorker(descriptor, output_dir)
        launch_qthread(window, "_windows_iso_preview_worker", worker)
        update = getattr(window, "_update_status", None)
        if callable(update):
            worker.progress.connect(update)
            worker.failed.connect(lambda message: update(f"Windows ISO preview failed: {message}"))
        worker.finished_ok.connect(
            lambda glb, warnings: self._show(
                window, asset, Path(glb), list(warnings), complete=False,
            )
        )
        worker.failed.connect(
            lambda message: QMessageBox.critical(window, "Windows ISO preview failed", message)
        )
        worker.start()
        return True

    def _show(
        self,
        window: object,
        asset: Asset,
        path: Path,
        warnings: list[str],
        *,
        complete: bool,
    ) -> None:
        update = getattr(window, "_update_status", None)
        if callable(update):
            suffix = f" ({len(warnings)} warning(s))" if warnings else ""
            update(f"Windows ISO model preview ready: {path.name}{suffix}")
        load = getattr(window, "_load_model_preview_glb", None)
        if callable(load):
            load(path, asset_id=asset.asset_id)
            register_inspector(
                window,
                glb_path=path,
                asset_id=asset.asset_id,
                complete=complete,
                on_load_all=(None if complete else lambda: self._load_full(window, asset)),
            )
            return
        preview = getattr(window, "preview", None)
        if preview is not None and hasattr(preview, "load_glb"):
            preview.load_glb(path, preview_platform_id="windows_iso")

    def _load_full(self, window: object, asset: Asset) -> None:
        try:
            descriptor = json.loads(asset.data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            QMessageBox.critical(window, "Windows ISO preview failed", str(exc))
            return
        output_dir = (
            project_root() / "exports" / "windows_iso_previews"
            / asset.asset_id / "all_animations"
        )
        worker = WindowsIsoPreviewWorker(
            descriptor, output_dir, full_animations=True,
        )
        launch_qthread(window, "_windows_iso_full_preview_worker", worker)
        update = getattr(window, "_update_status", None)
        if callable(update):
            update("Building complete Windows ISO animation preview…")
            worker.progress.connect(update)
            worker.failed.connect(
                lambda message: update(f"Windows ISO preview failed: {message}")
            )
        worker.finished_ok.connect(
            lambda glb, warnings: self._show(
                window, asset, Path(glb), list(warnings), complete=True,
            )
        )
        worker.failed.connect(
            lambda message: QMessageBox.critical(window, "Windows ISO preview failed", message)
        )
        worker.start()

    def supports_model_inspector(self, asset: Asset) -> bool:
        return asset.magic in {"WSMO", "WAM1"}

    def home_package_stub_message(self, asset: Asset) -> str | None:
        return None
