"""Tinder-style review dialog for unnamed LBX item models."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
)

from ...install import project_root
from ...core.qthread import stop_qthread
from .cgfx_preview import prepare_cgfx_model
from .lbx_catalog import LbxCatalog
from .lbx_item_review import (
    item_export_job,
    item_model_paths,
    load_review_state,
    proposed_item_title,
    sanitize_submission_title,
    save_review_state,
)
from .lbx_submission import descriptor_for_path, export_lbx_submission


class LbxItemReviewDialog(QDialog):
    def __init__(self, window: object, catalog: LbxCatalog, web_view: object) -> None:
        super().__init__(window)
        self.window = window
        self.catalog = catalog
        self.web_view = web_view
        self.state = load_review_state(catalog.rom_path)
        reviewed = set(self.state["decisions"])
        self.all_paths = item_model_paths(catalog.entries)
        self.pending = [path for path in self.all_paths if path not in reviewed]
        self.current_path: str | None = None
        self.setWindowTitle("LBX Item Review")
        self.setMinimumSize(880, 720)
        self.setModal(True)
        self._viewport_restored = False
        self._suspend_normal_preview()
        self._attach_live_viewport()
        self._build_ui()
        QTimer.singleShot(0, self._show_next)

    def _suspend_normal_preview(self) -> None:
        action = getattr(self.window, "auto_preview_action", None)
        self.auto_preview_was_enabled = bool(action is not None and action.isChecked())
        if action is not None:
            action.setChecked(False)
        timer = getattr(self.window, "preview_timer", None)
        if timer is not None:
            timer.stop()
        self.window._queued_preview_asset_id = None
        for attribute in ("_threeds_preview_worker", "preview_worker"):
            worker = getattr(self.window, attribute, None)
            if worker is None:
                continue
            for signal_name in ("finished_ok", "failed"):
                signal = getattr(worker, signal_name, None)
                if signal is not None:
                    try:
                        signal.disconnect()
                    except RuntimeError:
                        pass
            stop_qthread(worker)

    def _attach_live_viewport(self) -> None:
        self.viewport = self.web_view.parentWidget()
        self.viewport_parent = self.viewport.parentWidget() if self.viewport is not None else None
        self.viewport_layout = self.viewport_parent.layout() if self.viewport_parent is not None else None
        self.viewport_index = self.viewport_layout.indexOf(self.viewport) if self.viewport_layout is not None else -1
        self.viewport_stretch = (
            self.viewport_layout.stretch(self.viewport_index)
            if self.viewport_layout is not None and self.viewport_index >= 0
            else 1
        )
        if self.viewport_layout is not None:
            self.viewport_layout.removeWidget(self.viewport)
        if self.viewport is not None:
            self.viewport.setParent(self)
            self.viewport.show()

    def _restore_live_viewport(self) -> None:
        if self._viewport_restored or self.viewport is None or self.viewport_layout is None:
            return
        self._viewport_restored = True
        self.layout().removeWidget(self.viewport)
        self.viewport.setParent(self.viewport_parent)
        if hasattr(self.viewport_layout, "insertWidget") and self.viewport_index >= 0:
            self.viewport_layout.insertWidget(self.viewport_index, self.viewport, self.viewport_stretch)
        else:
            self.viewport_layout.addWidget(self.viewport)
        self.viewport.show()

    def done(self, result: int) -> None:
        self._restore_live_viewport()
        action = getattr(self.window, "auto_preview_action", None)
        if action is not None and self.auto_preview_was_enabled:
            action.setChecked(True)
        super().done(result)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        heading = QHBoxLayout()
        title = QLabel("LBX Item Review")
        title.setStyleSheet("font-size: 20px; font-weight: 700;")
        self.counter = QLabel()
        self.counter.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        heading.addWidget(title)
        heading.addStretch(1)
        heading.addWidget(self.counter)
        layout.addLayout(heading)

        if self.viewport is not None:
            self.viewport.setMinimumHeight(510)
            layout.addWidget(self.viewport, 1)
        else:
            missing = QLabel("Live preview viewport is unavailable.")
            missing.setAlignment(Qt.AlignmentFlag.AlignCenter)
            missing.setMinimumHeight(510)
            layout.addWidget(missing, 1)

        self.source = QLabel()
        self.source.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.source.setStyleSheet("color: #8b949e;")
        layout.addWidget(self.source)

        self.name = QLineEdit()
        self.name.setPlaceholderText("Descriptive submission name")
        self.name.returnPressed.connect(self._keep_current)
        layout.addWidget(self.name)

        self.status = QLabel(
            "Export queues this model. After the last decision, all queued packages export automatically."
        )
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.finish_button = QPushButton("Finish and export queued")
        self.finish_button.clicked.connect(self._export_queued)
        self.skip_button = QPushButton("Skip")
        self.skip_button.clicked.connect(self._skip_current)
        self.keep_button = QPushButton("Export")
        self.keep_button.setDefault(True)
        self.keep_button.clicked.connect(self._keep_current)
        buttons.addWidget(self.finish_button)
        buttons.addStretch(1)
        buttons.addWidget(self.skip_button)
        buttons.addWidget(self.keep_button)
        layout.addLayout(buttons)

    def _report(self, message: str) -> None:
        self.status.setText(message)
        update = getattr(self.window, "_update_status", None)
        if callable(update):
            update(message)
        QApplication.processEvents()

    def _queued(self) -> list[tuple[str, dict]]:
        return [
            (path, decision)
            for path, decision in self.state["decisions"].items()
            if decision.get("status") == "selected" and not decision.get("exported")
        ]

    def _update_counter(self) -> None:
        reviewed = len(self.all_paths) - len(self.pending)
        self.counter.setText(
            f"{reviewed}/{len(self.all_paths)} reviewed · {len(self._queued())} queued"
        )

    def _show_next(self) -> None:
        self._update_counter()
        if not self.pending:
            self.current_path = None
            self._export_queued()
            return
        self.current_path = self.pending[0]
        self.source.setText(f"RomFS: {self.current_path}")
        self.name.setText(proposed_item_title(self.current_path))
        self.name.selectAll()
        self.web_view.clear_scene()
        self._set_decision_buttons(False)
        try:
            cache = project_root() / "exports" / "threeds_cgfx_previews"
            prepared = prepare_cgfx_model(
                descriptor_for_path(self.catalog, self.current_path),
                cache,
                progress=self._report,
            )
            preview_widget = getattr(self.window, "preview", None)
            if preview_widget is None or not hasattr(preview_widget, "load_glb"):
                raise RuntimeError("RAE's normal model preview widget is unavailable")
            # Use the same public preview path as ordinary CGMD selection. Calling
            # WebGlbPreviewWidget directly leaves PreviewWidget's message/image
            # overlays visible and the WebEngine renderer hidden underneath.
            preview_widget.load_glb(prepared.glb_path, preview_platform_id="3ds")
            name = Path(self.current_path).stem
            self._report(f"Showing {name}. Name it, choose Export to keep it, or Skip it. Drag to rotate.")
        except Exception as exc:
            self._skip_failed_current(exc)
            return
        self._set_decision_buttons(True)
        self.name.setFocus()

    def _skip_failed_current(self, error: Exception) -> None:
        if not self.current_path:
            return
        path = self.current_path
        self.state["decisions"][path] = {
            "status": "skipped",
            "automatic": True,
            "reason": str(error),
        }
        save_review_state(self.state)
        self.pending.pop(0)
        self._report(f"Automatically skipped unreadable item: {path}")
        QTimer.singleShot(0, self._show_next)

    def _set_decision_buttons(self, enabled: bool) -> None:
        self.name.setEnabled(enabled)
        self.skip_button.setEnabled(enabled)
        self.keep_button.setEnabled(enabled)

    def _skip_current(self) -> None:
        if not self.current_path:
            return
        self.state["decisions"][self.current_path] = {"status": "skipped"}
        save_review_state(self.state)
        self.pending.pop(0)
        self._show_next()

    def _keep_current(self) -> None:
        if not self.current_path:
            return
        title = sanitize_submission_title(self.name.text())
        if not title:
            QMessageBox.warning(self, "Name required", "Enter a descriptive submission name first.")
            return
        duplicate = next(
            (
                path for path, decision in self.state["decisions"].items()
                if path != self.current_path
                and decision.get("status") == "selected"
                and str(decision.get("title", "")).casefold() == title.casefold()
            ),
            None,
        )
        if duplicate:
            QMessageBox.warning(self, "Duplicate name", f"That name is already assigned to:\n{duplicate}")
            return
        self.state["decisions"][self.current_path] = {
            "status": "selected",
            "title": title,
            "exported": False,
        }
        save_review_state(self.state)
        self.pending.pop(0)
        self._show_next()

    def _export_queued(self) -> None:
        queued = self._queued()
        if not queued:
            if not self.pending:
                QMessageBox.information(self, "LBX item review", "Review complete. No item packages are waiting to export.")
                self.accept()
            else:
                QMessageBox.information(self, "Nothing queued", "Choose Export on at least one item first.")
            return
        progress = QProgressDialog("Preparing LBX item packages…", "Stop after current item", 0, len(queued), self)
        progress.setWindowTitle("Exporting reviewed LBX items")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        output = project_root() / "exports"
        exported = 0
        failed = 0

        def snapshot(path: Path, _job) -> bytes | None:
            return self.web_view.capture_snapshot_png(
                path,
                max(1, int(self.web_view.width())),
                max(1, int(self.web_view.height())),
                yaw_deg=30.0,
                pitch_deg=20.0,
                zoom_factor=0.82,
            )

        for index, (path, decision) in enumerate(queued, 1):
            if progress.wasCanceled():
                break
            title = str(decision["title"])
            progress.setLabelText(f"{index}/{len(queued)} · {title}")
            progress.setValue(index - 1)
            QApplication.processEvents()
            try:
                export_lbx_submission(
                    self.catalog,
                    item_export_job(path, title),
                    output,
                    snapshot,
                    progress=self._report,
                )
                decision["exported"] = True
                exported += 1
                save_review_state(self.state)
            except Exception as exc:
                decision.clear()
                decision.update({
                    "status": "skipped",
                    "automatic": True,
                    "reason": f"Export failed: {exc}",
                })
                failed += 1
                save_review_state(self.state)
        progress.setValue(len(queued))
        message = f"Exported {exported} item package(s) to:\n{output / 'LBX' / 'Items' / 'Props'}"
        if failed:
            message += f"\n\nAutomatically skipped {failed} model(s) that could not be exported."
        QMessageBox.information(self, "LBX item export finished", message)
        if not self.pending:
            self.accept()
        else:
            self._update_counter()


def start_lbx_item_review(window: object) -> None:
    if getattr(window, "_rom_platform_id", None) != "3ds" or not getattr(window, "rom_path", None):
        QMessageBox.information(window, "Not available", "Open the LBX 3DS ROM first.")
        return
    try:
        catalog = LbxCatalog(getattr(window, "rom_path"))
    except Exception as exc:
        QMessageBox.information(
            window,
            "Not an LBX ROM",
            f"The open 3DS ROM does not contain the expected LBX item catalog.\n\n{exc}",
        )
        return
    if not any(path.startswith("/3ddata/item/") for path in item_model_paths(catalog.entries)):
        QMessageBox.information(window, "Not an LBX item catalog", "This ROM has no LBX item models to review.")
        return
    preview = getattr(window, "preview", None)
    web_view = getattr(preview, "_web_view", None)
    if web_view is None or not getattr(web_view, "is_available", lambda: False)():
        QMessageBox.warning(window, "Previewer unavailable", "The Three.js/WebEngine previewer is required.")
        return
    web_view.set_preview_platform("3ds")
    web_view.wait_until_api_ready()
    dialog = LbxItemReviewDialog(window, catalog, web_view)
    dialog.exec()
