from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from ....core.assets import Asset
from ....core.modules.types import PreviewContext, PreviewRoute
from ....install import project_root
from ..service import build_model_glb


class SwitchModelPreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, descriptor: dict, output_dir: Path):
        super().__init__()
        self.descriptor = descriptor
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            glb = build_model_glb(self.descriptor, self.output_dir / "preview.glb", progress=self.progress.emit)
            self.finished_ok.emit(str(glb))
        except Exception as exc:
            self.failed.emit(str(exc))


class SwitchModelModule:
    platform_id = "switch"

    def _descriptor(self, asset: Asset) -> dict:
        try:
            return json.loads(asset.data.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic == "TRMD":
            return PreviewRoute.MODEL
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        if asset.magic != "TRMD":
            return False
        descriptor = self._descriptor(asset)
        if descriptor.get("type") != "trinity_file":
            return False
        window = ctx.window
        output_dir = project_root() / "exports" / "switch_model_previews" / asset.asset_id
        output_dir.mkdir(parents=True, exist_ok=True)
        worker = SwitchModelPreviewWorker(descriptor, output_dir)
        window._switch_preview_worker = worker
        if hasattr(window, "_update_status"):
            worker.progress.connect(window._update_status)
        worker.finished_ok.connect(lambda glb: self._show(window, asset, glb))
        worker.failed.connect(
            lambda message: QMessageBox.critical(window, "Switch model preview failed", message)
        )
        worker.start()
        return True

    def _show(self, window, asset: Asset, glb_path: str) -> None:
        path = Path(glb_path)
        if hasattr(window, "_update_status"):
            window._update_status(f"Switch model preview ready: {path.name}")
        if hasattr(window, "_load_model_preview_glb"):
            window._load_model_preview_glb(path, asset_id=asset.asset_id)
            if hasattr(window, "_update_preview_details"):
                window._update_preview_details(asset)
            return
        preview = getattr(window, "preview", None)
        if preview is not None and hasattr(preview, "load_glb"):
            preview.load_glb(path)

    def supports_model_inspector(self, asset: Asset) -> bool:
        return asset.magic == "TRMD"

    def home_package_stub_message(self, asset: Asset) -> str | None:
        return None
