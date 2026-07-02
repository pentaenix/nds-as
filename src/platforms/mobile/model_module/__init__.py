from __future__ import annotations

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import QMessageBox

from ....core.modules.protocols import ModelModule
from ....core.modules.types import PreviewContext, PreviewRoute
from ....install import project_root
from ...home.assetstudio_preview import assetstudio_available
from ..mesh_export import unitypy_available
from ....scanner import Asset
from .preview import build_mobile_asset_preview
from .viewport import default_output_dir, finish_preview

_MOBILE_MODEL_MAGICS = frozenset({"UNITY", "HOME", "ABA"})


class MobilePreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, asset, output_dir):
        super().__init__()
        self.asset = asset
        self.output_dir = output_dir

    def run(self) -> None:
        try:
            result = build_mobile_asset_preview(self.asset, self.output_dir, progress=self.progress.emit)
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


class MobileModelModule:
    platform_id = "mobile"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic in _MOBILE_MODEL_MAGICS:
            return PreviewRoute.MOBILE_MODEL
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        window = ctx.window
        if asset.magic not in _MOBILE_MODEL_MAGICS:
            return False
        if not self._preview_tools_available(asset, window):
            return True
        output_dir = default_output_dir()
        worker = MobilePreviewWorker(asset, output_dir)
        window._mobile_preview_worker = worker
        if hasattr(window, "_focus_terminal"):
            window._focus_terminal(
                banner=(
                    f"Preparing mobile model preview for "
                    f"{getattr(asset, 'virtual_path', getattr(asset, 'asset_id', 'asset'))}…\n"
                    "Exports mesh GLB plus HOME Cache textures (_col/_emi) when AssetStudio is available."
                )
            )
        if hasattr(window, "_update_status"):
            worker.progress.connect(window._update_status)
            worker.failed.connect(lambda message: window._update_status(f"Mobile model preview failed: {message}"))
        worker.finished_ok.connect(lambda result: finish_preview(window, asset, result))
        worker.failed.connect(lambda message: QMessageBox.critical(window, "Mobile model preview failed", message))
        worker.start()
        return True

    def supports_model_inspector(self, asset: Asset) -> bool:
        return asset.magic in _MOBILE_MODEL_MAGICS

    def home_package_stub_message(self, asset: Asset) -> str | None:
        if asset.magic != "HOME":
            return None
        return (
            "Pokémon HOME package selected.\n\n"
            "This patch inventories model/texture/rig/animation candidates and groups dependencies. "
            "Use Device Toolkit → Mobile → Preview Selected Mobile Model for viewport export."
        )

    def _preview_tools_available(self, asset: Asset, window) -> bool:
        magic = getattr(asset, "magic", "")
        if magic == "ABA" and not unitypy_available() and not assetstudio_available():
            QMessageBox.warning(
                window,
                "Preview tools required",
                "HOME ABA previews need UnityPy and/or AssetStudioModCLI.\n\n"
                "  python -m pip install UnityPy\n"
                "  set RAE_ASSETSTUDIO_CLI to AssetStudioModCLI (see rae/tools README)",
            )
            return False
        if magic != "ABA" and not unitypy_available() and not assetstudio_available():
            QMessageBox.warning(
                window,
                "Preview tools required",
                "Install UnityPy and/or AssetStudioModCLI for mobile model previews.",
            )
            return False
        return True
