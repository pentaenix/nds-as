"""EasyFind MainWindow mixin."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ...easyfind import (
    easyfind_path_for_game_code,
    is_valid_game_code,
    load_easyfind_quick_open,
    normalize_game_code,
    read_nds_rom_identity,
    validate_easyfind,
)
from ..easyfind import EasyFindWorkspace
from ..workers.easyfind import EasyFindBuildResult, EasyFindBuildWorker


class EasyFindActionsMixin:
    """Toolbar action and workspace switching for EasyFind."""

    def _init_easyfind_state(self) -> None:
        self.easyfind_path: str | None = None
        self.easyfind_worker: EasyFindBuildWorker | None = None
        self._easyfind_build_stages: list[str] = []
        self._easyfind_build_percent = 0
        self._in_easyfind_workspace = False

    def install_easyfind_actions(self) -> None:
        """Add toolbar action and EasyFind workspace to the window."""
        from PySide6.QtGui import QAction

        self.easyfind_action = QAction("EasyFind Map", self)
        self.easyfind_action.setVisible(False)
        self.easyfind_action.triggered.connect(self.open_easyfind_workspace)

        self.easyfind_back_action = QAction("← Back to Browser", self)
        self.easyfind_back_action.setVisible(False)
        self.easyfind_back_action.triggered.connect(self.return_to_browser_workspace)

        self.easyfind_build_action = QAction("Build EasyFind", self)
        self.easyfind_build_action.setVisible(False)
        self.easyfind_build_action.triggered.connect(self.build_easyfind_for_current_assets)

        if hasattr(self, "main_toolbar"):
            self.main_toolbar.addAction(self.easyfind_action)
            self.main_toolbar.addAction(self.easyfind_back_action)
            self.main_toolbar.addAction(self.easyfind_build_action)

        self.easyfind_workspace = EasyFindWorkspace()
        self.easyfind_workspace.panel.validate_requested.connect(self.validate_current_easyfind)

        if hasattr(self, "workspace_stack"):
            self.workspace_stack.addWidget(self.easyfind_workspace)

        self._update_main_toolbar()

    def _update_main_toolbar(self) -> None:
        """Show toolbar actions appropriate for browser vs EasyFind workspace."""
        if not hasattr(self, "open_rom_toolbar_action"):
            return

        loaded = bool(self.assets)
        in_easyfind = self._in_easyfind_workspace
        can_build = loaded and self._canonical_easyfind_path() is not None

        if in_easyfind:
            self.open_rom_toolbar_action.setVisible(False)
            self.save_session_toolbar_action.setVisible(False)
            self.easyfind_action.setVisible(False)
            self.easyfind_back_action.setVisible(True)
            self.easyfind_build_action.setVisible(can_build)
        else:
            self.open_rom_toolbar_action.setVisible(True)
            self.save_session_toolbar_action.setVisible(loaded)
            self.easyfind_action.setVisible(loaded and self._current_game_code() != "")
            self.easyfind_back_action.setVisible(False)
            self.easyfind_build_action.setVisible(False)

    def _current_game_code(self) -> str:
        code = normalize_game_code(getattr(self, "rom_game_code", ""))
        if code:
            return code
        if self.rom_path:
            try:
                code, title = read_nds_rom_identity(self.rom_path)
                self.rom_game_code = code
                if not getattr(self, "rom_title", ""):
                    self.rom_title = title
                return code
            except Exception:
                return ""
        return ""

    def _canonical_easyfind_path(self) -> Path | None:
        code = self._current_game_code()
        if not is_valid_game_code(code):
            return None
        return easyfind_path_for_game_code(code)

    def _bind_easyfind_for_current_game(self, *, log: bool = False) -> None:
        """Resolve the canonical EasyFind file for the current ROM or session."""
        path = self._canonical_easyfind_path()
        if path is None:
            self.easyfind_path = None
            return
        if path.is_file():
            self.easyfind_path = str(path)
            if log:
                self._update_status(f"EasyFind index available: {path.name}")
        else:
            self.easyfind_path = None
            if log:
                self._update_status(
                    f"No EasyFind index for game code {path.stem} yet. "
                    f"Expected: easyfind/{path.name}"
                )

    def open_easyfind_workspace(self) -> None:
        """Switch to the EasyFind workspace."""
        if not hasattr(self, "easyfind_workspace"):
            return
        if not self.assets:
            return
        self._bind_easyfind_for_current_game()
        self._in_easyfind_workspace = True
        self.workspace_stack.setCurrentWidget(self.easyfind_workspace)
        self._update_main_toolbar()
        self._refresh_easyfind_panel()
        self.easyfind_workspace.build_panel.raise_()

    def return_to_browser_workspace(self) -> None:
        """Return to the normal browser workspace."""
        if hasattr(self, "workspace_stack") and hasattr(self, "browser_workspace"):
            self._in_easyfind_workspace = False
            self.workspace_stack.setCurrentWidget(self.browser_workspace)
            self._update_main_toolbar()

    def _refresh_easyfind_panel(self) -> None:
        panel = self.easyfind_workspace.panel
        if not self.assets:
            panel.show_no_assets()
            return

        canonical = self._canonical_easyfind_path()
        if canonical is None:
            panel.show_error(
                "Could not read a Nintendo DS game code from this ROM or session.\n\n"
                "EasyFind files are stored as easyfind/<GAME_CODE>.easyfind."
            )
            panel.raise_()
            return

        existing = canonical if canonical.is_file() else None
        if existing is not None:
            try:
                quick_open = load_easyfind_quick_open(existing)
                validation = validate_easyfind(existing)
                panel.show_existing_file(existing, quick_open, validation)
                self.easyfind_path = str(existing)
                panel.raise_()
                return
            except Exception as exc:
                panel.show_error(
                    f"Could not quick-open EasyFind file:\n{existing}\n\n{exc}"
                )
                panel.raise_()
                return

        panel.show_build_required(canonical)
        panel.raise_()

    def build_easyfind_for_current_assets(self) -> None:
        """Build a .easyfind file from the currently loaded assets."""
        if not self.assets:
            QMessageBox.information(
                self,
                "EasyFind",
                "Load a ROM or session before building EasyFind.",
            )
            return

        output_path = self._canonical_easyfind_path()
        if output_path is None:
            QMessageBox.warning(
                self,
                "EasyFind",
                "Could not determine the Nintendo DS game code for this ROM or session.",
            )
            return

        if self.easyfind_worker is not None and self.easyfind_worker.isRunning():
            return

        if output_path.is_file():
            answer = QMessageBox.question(
                self,
                "Rebuild EasyFind",
                f"Replace the existing EasyFind index?\n\n{output_path}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        rom_title = getattr(self, "rom_title", "") or ""
        rom_game_code = self._current_game_code()
        if self.rom_path and not rom_title:
            try:
                rom_game_code, rom_title = read_nds_rom_identity(self.rom_path)
                self.rom_game_code = rom_game_code
                self.rom_title = rom_title
            except Exception:
                pass

        panel = self.easyfind_workspace.panel
        panel.show_building(stage="Creating EasyFind document…", percent=10)

        self.easyfind_worker = EasyFindBuildWorker(
            assets=self.assets,
            output_path=output_path,
            rom_path=self.rom_path,
            platform="nds",
            rom_title=rom_title,
            rom_game_code=rom_game_code,
        )
        self.easyfind_worker.progress.connect(self._on_easyfind_build_progress)
        self.easyfind_worker.finished.connect(self._on_easyfind_build_finished)
        self.easyfind_worker.failed.connect(self._on_easyfind_build_failed)
        self.easyfind_worker.start()

    def _on_easyfind_build_progress(self, message: str) -> None:
        stage_map = {
            "Creating EasyFind document…": 15,
            "Creating output container…": 30,
            "Writing manifest…": 40,
            "Writing index…": 55,
            "Writing annotations…": 65,
            "Writing preview index…": 75,
            "Validating preview hashes…": 85,
            "Finalizing EasyFind file…": 90,
            "Writing .easyfind container…": 50,
            "Validating EasyFind…": 95,
            "Done.": 100,
        }
        percent = stage_map.get(message, self.easyfind_workspace.panel.progress_bar.value())
        self.easyfind_workspace.panel.update_build_progress(message, percent)
        self._update_status(message)

    def _on_easyfind_build_finished(self, result: object) -> None:
        if not isinstance(result, EasyFindBuildResult):
            return
        self.easyfind_path = str(result.path)
        panel = self.easyfind_workspace.panel
        panel.show_build_success(result.path, result.quick_open, result.validation)
        panel.raise_()
        status = "EasyFind built successfully."
        if not result.validation.ok:
            status = "EasyFind built but validation reported issues."
        self._update_status(f"{status} {result.path}")

    def _on_easyfind_build_failed(self, message: str) -> None:
        self.easyfind_workspace.panel.show_error(message)
        self.easyfind_workspace.panel.raise_()
        self._update_status(f"EasyFind build failed: {message}")

    def validate_current_easyfind(self) -> None:
        """Re-validate the current EasyFind file."""
        path = self._canonical_easyfind_path()
        if path is None or not path.is_file():
            QMessageBox.information(self, "EasyFind", "No EasyFind file to validate.")
            return

        try:
            quick_open = load_easyfind_quick_open(path)
            validation = validate_easyfind(path)
            self.easyfind_path = str(path)
            self.easyfind_workspace.panel.show_validation_result(path, quick_open, validation)
            self.easyfind_workspace.panel.raise_()
            if validation.ok:
                self._update_status(f"EasyFind validation OK: {path}")
            else:
                self._update_status(f"EasyFind validation failed: {path}")
        except Exception as exc:
            self.easyfind_workspace.panel.show_error(str(exc))
            self.easyfind_workspace.panel.raise_()
            self._update_status(f"EasyFind validation error: {exc}")
