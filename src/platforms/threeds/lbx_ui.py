"""Qt actions for LBX batch exports."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox

from ...install import project_root
from .game_catalog import LBX_PRODUCT_CODES
from .lbx_catalog import LbxCatalog
from .lbx_submission import export_lbx_jobs, is_lbx_upright_weapon
from .rom import load_descriptor


def _live_menu(window: object, title: str):
    """Resolve a current QMenu wrapper from its menubar action.

    macOS can replace native QMenu wrappers after menu construction, leaving a
    previously stored Python attribute attached to an already deleted object.
    """
    menubar = getattr(window, "menuBar", lambda: None)()
    if menubar is None:
        return None
    for menu_action in menubar.actions():
        if menu_action.text().replace("&", "") != title:
            continue
        menu = menu_action.menu()
        if menu is not None:
            refs = getattr(window, "_menu_lifetime_refs", None)
            if isinstance(refs, list):
                refs.extend((menu_action, menu))
            return menu
    return None


def install_lbx_export_ui(window: object) -> None:
    lbx_menu = _live_menu(window, "LBX")
    if lbx_menu is None:
        menubar = getattr(window, "menuBar", lambda: None)()
        if menubar is None:
            return
        lbx_menu = menubar.addMenu("&LBX")
        refs = getattr(window, "_menu_lifetime_refs", None)
        if isinstance(refs, list):
            refs.extend((lbx_menu.menuAction(), lbx_menu))
    window._lbx_menu = lbx_menu
    if getattr(window, "_lbx_export_directory_action", None) is None:
        action = QAction("Export Directory…", window)
        action.setToolTip(
            "Export the selected LBX folder as categorized Models Resource-ready DAE packages."
        )
        action.triggered.connect(lambda: _start_directory_export(window))
        lbx_menu.addAction(action)
        window._lbx_export_directory_action = action
    if getattr(window, "_lbx_item_review_action", None) is None:
        review_action = QAction("Review Item Models…", window)
        review_action.setToolTip(
            "Review unnamed /3ddata/item models, name the useful props, and export submission packages."
        )
        review_action.triggered.connect(lambda: _start_item_review(window))
        lbx_menu.addAction(review_action)
        window._lbx_item_review_action = review_action
    if not getattr(window, "_lbx_toolkit_sync_registered", False):
        callbacks = getattr(window, "_platform_toolkit_sync_callbacks", None)
        if not isinstance(callbacks, list):
            callbacks = []
            window._platform_toolkit_sync_callbacks = callbacks
        callbacks.append(lambda: sync_lbx_export_ui(window))
        window._lbx_toolkit_sync_registered = True
    sync_lbx_export_ui(window)


def sync_lbx_export_ui(window: object) -> None:
    menu = getattr(window, "_lbx_menu", None)
    if menu is None:
        return
    code = str(getattr(window, "rom_game_code", "") or "").strip().upper()
    rom_name = Path(str(getattr(window, "rom_path", "") or "")).name.casefold()
    enabled = bool(
        getattr(window, "_rom_platform_id", None) == "3ds"
        and (
            code in LBX_PRODUCT_CODES
            or rom_name.startswith("lbx")
            or "little battlers experience" in rom_name
        )
    )
    menu.setEnabled(enabled)
    for action in (
        getattr(window, "_lbx_export_directory_action", None),
        getattr(window, "_lbx_item_review_action", None),
    ):
        if action is not None:
            action.setEnabled(enabled)


def _start_item_review(window: object) -> None:
    from .lbx_item_review_ui import start_lbx_item_review

    start_lbx_item_review(window)


def _selected_lbx_paths(window: object) -> set[str]:
    folder = getattr(window, "selected_folder", lambda: None)()
    if folder is None:
        return set()
    parts, raw = folder
    assets = getattr(window, "_folder_assets", lambda *_args: [])(parts, raw)
    paths: set[str] = set()
    for asset in assets:
        if getattr(asset, "magic", None) != "CGMD":
            continue
        descriptor = load_descriptor(asset) or {}
        if descriptor.get("game") == "lbx" and descriptor.get("romfs_path"):
            paths.add(str(descriptor["romfs_path"]))
    return paths


def _start_directory_export(window: object) -> None:
    if getattr(window, "_rom_platform_id", None) != "3ds" or not getattr(window, "rom_path", None):
        QMessageBox.information(window, "Not available", "Open the LBX 3DS ROM first.")
        return
    selected_paths = _selected_lbx_paths(window)
    if not selected_paths:
        QMessageBox.information(
            window,
            "Select an LBX folder",
            "Select a folder containing LBX models in Mapped Tree or Raw Folders first.",
        )
        return
    catalog = LbxCatalog(getattr(window, "rom_path"))
    jobs = [
        job for job in catalog.build_jobs()
        if any(path in selected_paths for path in job.source_paths)
    ]
    if not jobs:
        QMessageBox.information(
            window,
            "No exportable LBX models",
            "This folder has no Chips, Parts, or Weapons selected by the LBX export profile.",
        )
        return
    default = project_root() / "exports"
    default.mkdir(parents=True, exist_ok=True)
    selected = QFileDialog.getExistingDirectory(
        window,
        f"Export {len(jobs)} LBX Models Resource packages",
        str(default),
    )
    if not selected:
        return

    preview = getattr(window, "preview", None)
    web_view = getattr(preview, "_web_view", None)
    if web_view is None or not getattr(web_view, "is_available", lambda: False)():
        QMessageBox.warning(
            window,
            "Previewer unavailable",
            "The Three.js/WebEngine previewer is required for transparent icons.",
        )
        return
    web_view.set_preview_platform("3ds")
    web_view.wait_until_api_ready()

    def snapshot(path: Path, job) -> bytes | None:
        top_level = job.category[0].casefold()
        if is_lbx_upright_weapon(job):
            yaw, pitch = 0.0, 0.0
        else:
            yaw = 14.0 if top_level == "parts" else 30.0
            pitch = 43.0 if top_level == "chips" else 15.0 if top_level == "parts" else 27.0
        return web_view.capture_snapshot_png(
            path,
            max(1, int(web_view.width())),
            max(1, int(web_view.height())),
            yaw_deg=yaw,
            pitch_deg=pitch,
            zoom_factor=0.82,
        )

    update = getattr(window, "_update_status", None)
    def progress(message: str) -> None:
        if callable(update):
            update(message)
        QApplication.processEvents()

    progress(f"LBX directory export started: {len(jobs)} package(s)")
    try:
        report = export_lbx_jobs(
            catalog,
            jobs,
            Path(selected),
            snapshot,
            progress=progress,
        )
    except Exception as exc:
        _fail(window, str(exc))
        return
    _finish(window, Path(selected), report)


def _finish(window: object, output: Path, report: dict) -> None:
    message = (
        f"Exported {len(report['exported'])} LBX package(s) to:\n{output / 'LBX'}\n\n"
        f"Skipped: {len(report['skipped'])}\nErrors: {len(report['errors'])}"
    )
    QMessageBox.information(window, "LBX directory export complete", message)
    update = getattr(window, "_update_status", None)
    if callable(update):
        update(message.replace("\n", " "))


def _fail(window: object, message: str) -> None:
    QMessageBox.critical(window, "LBX directory export failed", message)
    update = getattr(window, "_update_status", None)
    if callable(update):
        update(f"LBX directory export failed: {message}")
