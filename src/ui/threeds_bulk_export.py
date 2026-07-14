"""Ultra Moon–only bulk Pokémon GLB export UI (wired through PlatformDispatch)."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QMessageBox,
    QVBoxLayout,
)

from ..core.modules import PlatformDispatch
from ..core.qthread import launch_qthread


class ThreedsPokemonBulkExportWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        *,
        host: object,
        assets: list,
        rom_path: str,
        out_dir: Path,
        shiny: bool,
        rom_platform_id: str,
    ) -> None:
        super().__init__()
        self.host = host
        self.assets = assets
        self.rom_path = rom_path
        self.out_dir = out_dir
        self.shiny = shiny
        self.rom_platform_id = rom_platform_id

    def run(self) -> None:
        try:
            result = PlatformDispatch.run_pokemon_bulk_export(
                self.host,
                self.assets,
                self.rom_path,
                self.out_dir,
                rom_platform_id=self.rom_platform_id,
                shiny=self.shiny,
                progress=self.progress.emit,
            )
            self.finished_ok.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc))


def create_bulk_export_action(window: object) -> QAction:
    """Create (or return) the Advanced-menu bulk export action."""
    action = getattr(window, "_threeds_pokemon_bulk_export_action", None)
    if action is not None:
        return action
    action = QAction("Bulk Export All Pokémon (GLB)…", window)
    action.setToolTip(
        "Export every Pokémon species to GLB files using the same pipeline as "
        "Export Selected (animations, all forms, normal + shiny textures). "
        "Ultra Moon only."
    )
    action.triggered.connect(lambda: _start_bulk_export(window))
    action.setEnabled(False)
    window._threeds_pokemon_bulk_export_action = action
    return action


def install_threeds_bulk_export_ui(window: object) -> None:
    """Wire toolbar button + visibility sync for bulk Pokémon export."""
    action = getattr(window, "_threeds_pokemon_bulk_export_action", None)
    if action is None:
        advanced_menu = getattr(window, "advanced_menu", None)
        if advanced_menu is None:
            menubar = window.menuBar()
            advanced_menu = _menu_named(menubar, "Advanced")
        if advanced_menu is None:
            return
        action = create_bulk_export_action(window)
        advanced_menu.addSeparator()
        advanced_menu.addAction(action)

    window.sync_threeds_pokemon_bulk_export_ui = (
        lambda *args, **kwargs: sync_threeds_pokemon_bulk_export_ui(window, *args, **kwargs)
    )

    button = getattr(window, "_threeds_pokemon_bulk_export_button", None)
    if button is None and hasattr(window, "export_button"):
        from PySide6.QtWidgets import QPushButton

        button = QPushButton("Bulk Pokémon…")
        button.setToolTip(action.toolTip())
        button.clicked.connect(action.trigger)
        button.setEnabled(False)
        layout = window.export_button.parentWidget().layout()
        if layout is not None:
            layout.insertWidget(layout.indexOf(window.export_button) + 1, button)
        window._threeds_pokemon_bulk_export_button = button

    sync_threeds_pokemon_bulk_export_ui(window)


def sync_threeds_pokemon_bulk_export_ui(window: object, *, visible: bool | None = None) -> None:
    action = getattr(window, "_threeds_pokemon_bulk_export_action", None)
    button = getattr(window, "_threeds_pokemon_bulk_export_button", None)
    if action is None and button is None:
        return
    if visible is None:
        rom_path = getattr(window, "rom_path", None)
        assets = getattr(window, "assets", None) or []
        platform_id = getattr(window, "_rom_platform_id", None)
        visible = bool(
            rom_path
            and platform_id == "3ds"
            and PlatformDispatch.supports_pokemon_bulk_export(
                rom_path=rom_path,
                rom_platform_id=platform_id,
                assets=assets,
                product_code=getattr(window, "rom_game_code", None),
            )
        )
    if action is not None:
        # macOS native menu bar ignores setVisible() on QAction after first hide —
        # keep the item in Advanced always and gate with enabled state instead.
        action.setEnabled(visible)
    if button is not None:
        button.setVisible(visible)
        button.setEnabled(visible)
    update = getattr(window, "_update_status", None)
    if callable(update) and visible:
        update("Bulk Pokémon export is available under Advanced → Bulk Export All Pokémon (GLB)…")


def _start_bulk_export(window: object) -> None:
    rom_path = getattr(window, "rom_path", None)
    assets = getattr(window, "assets", None) or []
    platform_id = getattr(window, "_rom_platform_id", None)
    if not rom_path or platform_id != "3ds":
        QMessageBox.information(
            window,
            "Not available",
            "Bulk Pokémon export is only available while Pokémon Ultra Moon is loaded.",
        )
        return
    if not PlatformDispatch.supports_pokemon_bulk_export(
        rom_path=rom_path,
        rom_platform_id=platform_id,
        assets=assets,
        product_code=getattr(window, "rom_game_code", None),
    ):
        QMessageBox.information(
            window,
            "Not available",
            "This ROM is not Pokémon Ultra Moon, or it has no Pokémon model rows to export.",
        )
        return

    shiny = _ask_bulk_export_options(window)
    if shiny is None:
        return

    default_dir = Path.cwd() / "exports" / "ultra_moon_pokemon"
    default_dir.mkdir(parents=True, exist_ok=True)
    out_dir = QFileDialog.getExistingDirectory(
        window,
        "Choose folder for Pokémon GLB export",
        str(default_dir),
    )
    if not out_dir:
        return

    if hasattr(window, "info_tabs") and hasattr(window, "log_box"):
        window.info_tabs.setCurrentWidget(window.log_box)

    worker = ThreedsPokemonBulkExportWorker(
        host=window,
        assets=assets,
        rom_path=str(rom_path),
        out_dir=Path(out_dir),
        shiny=shiny,
        rom_platform_id=platform_id,
    )
    launch_qthread(window, "_threeds_pokemon_bulk_export_worker", worker)
    if hasattr(window, "_update_status"):
        worker.progress.connect(window._update_status)
    worker.finished_ok.connect(lambda result: _bulk_export_finished(window, Path(out_dir), result))
    worker.failed.connect(
        lambda message: QMessageBox.critical(window, "Bulk Pokémon export failed", message)
    )
    worker.start()
    if hasattr(window, "_update_status"):
        window._update_status(f"Bulk Pokémon export started → {out_dir}")


def _bulk_export_finished(window: object, out_dir: Path, result) -> None:
    msg = (
        f"Exported {len(result.written)} Pokémon GLB file(s) to:\n{out_dir}\n\n"
        f"Species processed: {result.species_count}"
    )
    if result.errors:
        msg += f"\n\n{len(result.errors)} species failed — see pokemon_bulk_export_manifest.json"
    if result.manifest_path is not None:
        msg += f"\n\nManifest: {result.manifest_path.name}"
    QMessageBox.information(window, "Bulk Pokémon export complete", msg)
    if hasattr(window, "_update_status"):
        window._update_status(
            f"Bulk Pokémon export complete: {len(result.written)} file(s), "
            f"{len(result.errors)} error(s)"
        )


def _ask_bulk_export_options(window: object) -> bool | None:
    dialog = QDialog(window)
    dialog.setWindowTitle("Bulk Export All Pokémon")
    layout = QVBoxLayout(dialog)
    header = QLabel(
        "Export one GLB per species using the same pipeline as Export Selected.\n"
        "Each file includes skeleton animations, every form/pattern, and embedded "
        "normal + shiny texture sets."
    )
    header.setWordWrap(True)
    layout.addWidget(header)
    shiny_box = QCheckBox("Default color variant: shiny (both sets are always embedded)")
    shiny_box.setChecked(bool(getattr(window, "_export_glb_shiny", False)))
    layout.addWidget(shiny_box)
    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.accepted.connect(dialog.accept)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    if dialog.exec() != QDialog.Accepted:
        return None
    return shiny_box.isChecked()


def _menu_named(menubar, title: str):
    for action in menubar.actions():
        menu = action.menu()
        if menu is not None and action.text().replace("&", "") == title:
            return menu
    return None
