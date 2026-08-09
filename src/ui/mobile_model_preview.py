"""Backward-compatible UI entry points for mobile model preview."""

from __future__ import annotations

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMessageBox

from ..platforms.mobile.model_module import MobileModelModule, MobilePreviewWorker
from ..platforms.mobile.model_module.preview import build_mobile_asset_preview
from ..platforms.mobile.model_module.viewport import default_output_dir, finish_preview, show_preview_in_viewport

_MENU_LIFETIME_REFS: list[object] = []


def install_mobile_model_preview_tools(window) -> None:
    menubar = window.menuBar()
    device_menu = _menu_named(menubar, "Device Toolkit")
    if device_menu is None:
        device_menu = menubar.addMenu("Device Toolkit")
        _MENU_LIFETIME_REFS.extend((device_menu.menuAction(), device_menu))
    mobile_menu = _submenu_named(device_menu, "Mobile")
    if mobile_menu is None:
        mobile_menu = device_menu.addMenu("Mobile")
        _MENU_LIFETIME_REFS.extend((mobile_menu.menuAction(), mobile_menu))

    for action in list(mobile_menu.actions()):
        if action.text().replace("&", "") == "Preview Selected Mobile Model":
            mobile_menu.removeAction(action)

    preview_action = QAction("Preview Selected Mobile Model", window)
    preview_action.triggered.connect(lambda: preview_selected_mobile_model(window))
    mobile_menu.addAction(preview_action)

    toolbar = getattr(window, "main_toolbar", None)
    if toolbar is not None:
        for action in toolbar.actions():
            if action.text().replace("&", "") == "Preview Mobile Model":
                break
        else:
            action = QAction("Preview Mobile Model", window)
            action.setToolTip("Build a textured GLB preview from the selected mobile UNITY/HOME/ABA asset.")
            action.triggered.connect(lambda: preview_selected_mobile_model(window))
            toolbar.addAction(action)


def preview_selected_mobile_model(window) -> None:
    from ..core.modules.types import PreviewContext

    asset_id = getattr(window, "_selected_asset_id", None)
    asset = getattr(window, "assets_by_id", {}).get(asset_id)
    if asset is None:
        QMessageBox.information(window, "No asset selected", "Select a UNITY, ABA, or HOME asset first.")
        return
    if getattr(asset, "magic", "") not in {"UNITY", "HOME", "ABA"}:
        QMessageBox.information(
            window,
            "Unsupported asset type",
            "Mobile model preview supports UNITY bundles, encrypted HOME .aba files, and grouped HOME package rows.",
        )
        return
    MobileModelModule().preview(PreviewContext(window=window, manual=True), asset)


def _menu_named(menubar, title: str):
    for action in menubar.actions():
        menu = action.menu()
        if menu is not None and action.text().replace("&", "") == title:
            _MENU_LIFETIME_REFS.extend((action, menu))
            return menu
    return None


def _submenu_named(menu, title: str):
    for action in menu.actions():
        sub = action.menu()
        if sub is not None and action.text().replace("&", "") == title:
            _MENU_LIFETIME_REFS.extend((action, sub))
            return sub
    return None


__all__ = [
    "MobilePreviewWorker",
    "build_mobile_asset_preview",
    "default_output_dir",
    "finish_preview",
    "install_mobile_model_preview_tools",
    "preview_selected_mobile_model",
    "show_preview_in_viewport",
]
