from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from ....core.assets import Asset
from ....core.modules.types import PreviewContext, PreviewRoute
from ....core.qthread import launch_qthread
from ....install import project_root
from ..rom import load_descriptor
from ..service import build_model_glb


class ThreedsModelPreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, descriptor: dict, output_dir):
        super().__init__()
        self.descriptor = descriptor
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            glb = build_model_glb(self.descriptor, self.output_dir, progress=self.progress.emit)
            self.finished_ok.emit(str(glb))
        except Exception as exc:
            self.failed.emit(str(exc))


class ThreedsModelModule:
    platform_id = "3ds"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic == "GFMD":
            return PreviewRoute.MODEL
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        if asset.magic != "GFMD":
            return False
        window = ctx.window
        descriptor = load_descriptor(asset)
        if not descriptor:
            return False
        output_dir = project_root() / "exports" / "threeds_model_previews"
        worker = ThreedsModelPreviewWorker(descriptor, output_dir)
        launch_qthread(window, "_threeds_preview_worker", worker)
        if hasattr(window, "_update_status"):
            worker.progress.connect(window._update_status)
        worker.finished_ok.connect(lambda glb: self._show(window, asset, glb))
        worker.failed.connect(
            lambda message: QMessageBox.critical(window, "3DS model preview failed", message)
        )
        worker.start()
        return True

    def _show(self, window, asset: Asset, glb_path: str) -> None:
        from pathlib import Path

        path = Path(glb_path)
        if hasattr(window, "_update_status"):
            window._update_status(f"3DS model preview ready: {path.name}")
        if hasattr(window, "_load_model_preview_glb"):
            window._load_model_preview_glb(path, asset_id=asset.asset_id)
            if hasattr(window, "show_threeds_model_tabs"):
                window.show_threeds_model_tabs(path, asset.asset_id)
            if hasattr(window, "_update_preview_details"):
                window._update_preview_details(asset)
            return
        preview = getattr(window, "preview", None)
        if preview is not None and hasattr(preview, "load_glb"):
            preview.load_glb(path)

    def supports_model_inspector(self, asset: Asset) -> bool:
        return asset.magic == "GFMD"

    def home_package_stub_message(self, asset: Asset) -> str | None:
        return None
