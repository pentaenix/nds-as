"""EasyFind MainWindow mixin."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ...easyfind import (
    EasyFindCanvasLayout,
    EasyFindDocument,
    EasyFindValidationReport,
    easyfind_path_for_game_code,
    is_valid_game_code,
    load_easyfind_quick_open,
    normalize_game_code,
    read_nds_rom_identity,
    validate_easyfind,
)
from ...easyfind.canvas_filters import FILTER_MODE_FOCUS, FOCUS_ANY, focus_filters_are_active
from ...easyfind.node_index import load_node_index
from ..easyfind import EasyFindWorkspace
from ..workers.easyfind import EasyFindBuildResult, EasyFindBuildWorker
from ..workers.easyfind_canvas_populate import EasyFindCanvasPopulateWorker
from ..workers.easyfind_cluster_expand import EasyFindClusterExpandWorker
from ..workers.easyfind_load import EasyFindLoadWorker
from ..workers.easyfind_preview_batch import EasyFindPreviewBatchWorker


class EasyFindActionsMixin:
    """Toolbar action and workspace switching for EasyFind."""

    def _init_easyfind_state(self) -> None:
        self.easyfind_path: str | None = None
        self.easyfind_worker: EasyFindBuildWorker | None = None
        self.easyfind_load_worker: EasyFindLoadWorker | None = None
        self.easyfind_populate_worker: EasyFindCanvasPopulateWorker | None = None
        self.easyfind_cluster_worker: EasyFindClusterExpandWorker | None = None
        self.easyfind_preview_worker: EasyFindPreviewBatchWorker | None = None
        self._easyfind_document: EasyFindDocument | None = None
        self._easyfind_build_stages: list[str] = []
        self._easyfind_build_percent = 0
        self._in_easyfind_workspace = False
        self._easyfind_canvas_ready = False
        self._easyfind_phase = "idle"
        self._easyfind_stage = ""
        self._easyfind_job_id = 0
        self._easyfind_preview_total = 0
        self._easyfind_view_states: dict[str, dict[str, float]] = {}
        self._easyfind_applied_filters = None
        self._easyfind_node_index = None
        self._model_web_snapshot = None

    def install_easyfind_actions(self) -> None:
        """Add toolbar action and EasyFind workspace to the window."""
        from PySide6.QtGui import QAction

        from ...model_preview.web_snapshot import ModelWebSnapshotService
        from ...ui.preview.web_preview import webengine_preview_available

        self._model_web_snapshot = (
            ModelWebSnapshotService(self)
            if webengine_preview_available()
            else None
        )

        self.easyfind_action = QAction("EasyFind Map", self)
        self.easyfind_action.setVisible(False)
        self.easyfind_action.triggered.connect(self.open_easyfind_workspace)

        self.easyfind_back_action = QAction("← Back to Browser", self)
        self.easyfind_back_action.setVisible(False)
        self.easyfind_back_action.triggered.connect(self.return_to_browser_workspace)

        self.easyfind_build_action = QAction("Build EasyFind", self)
        self.easyfind_build_action.setVisible(False)
        self.easyfind_build_action.triggered.connect(self.build_easyfind_for_current_assets)

        self.easyfind_fit_action = QAction("Fit All", self)
        self.easyfind_fit_action.setVisible(False)
        self.easyfind_fit_action.triggered.connect(self._easyfind_fit_all)

        self.easyfind_reset_action = QAction("Reset View", self)
        self.easyfind_reset_action.setVisible(False)
        self.easyfind_reset_action.triggered.connect(self._easyfind_reset_view)

        if hasattr(self, "main_toolbar"):
            self.main_toolbar.addAction(self.easyfind_action)
            self.main_toolbar.addAction(self.easyfind_back_action)
            self.main_toolbar.addAction(self.easyfind_build_action)
            self.main_toolbar.addAction(self.easyfind_fit_action)
            self.main_toolbar.addAction(self.easyfind_reset_action)

        ws = EasyFindWorkspace()
        self.easyfind_workspace = ws
        ws.panel.validate_requested.connect(self.validate_current_easyfind)
        ws.panel.build_requested.connect(self.build_easyfind_for_current_assets)
        ws.controls_panel.apply_requested.connect(self._apply_easyfind_canvas_filters)
        ws.controls_panel.fit_all_requested.connect(self._easyfind_fit_all)
        ws.controls_panel.reset_view_requested.connect(self._easyfind_reset_view)
        ws.canvas_view.node_selected.connect(self._on_easyfind_node_selected)
        ws.canvas_view.node_activated.connect(self._show_easyfind_node_in_browser)
        ws.canvas_view.show_in_browser_requested.connect(self._show_easyfind_node_in_browser)
        ws.canvas_view.cluster_expand_requested.connect(self._expand_easyfind_cluster)
        ws.canvas_view.cluster_collapse_requested.connect(self._collapse_easyfind_cluster)
        ws.inspector_panel.show_in_browser_requested.connect(self._show_easyfind_node_in_browser)
        ws.inspector_panel.filter_map_requested.connect(self._easyfind_filter_by_map)

        if hasattr(self, "workspace_stack"):
            self.workspace_stack.addWidget(ws)

        self._update_main_toolbar()

    def _update_main_toolbar(self) -> None:
        """Show toolbar actions appropriate for browser vs EasyFind workspace."""
        if not hasattr(self, "open_rom_toolbar_action"):
            return

        loaded = bool(self.assets)
        in_easyfind = self._in_easyfind_workspace
        can_build = loaded and self._canonical_easyfind_path() is not None
        building = self.easyfind_worker is not None and self.easyfind_worker.isRunning()
        canvas_ready = in_easyfind and self._easyfind_canvas_ready

        if in_easyfind:
            self.open_rom_toolbar_action.setVisible(False)
            self.save_session_toolbar_action.setVisible(False)
            self.easyfind_action.setVisible(False)
            self.easyfind_back_action.setVisible(True)
            self.easyfind_build_action.setVisible(can_build and not building)
            if building:
                self.easyfind_build_action.setText("Building EasyFind…")
                self.easyfind_build_action.setEnabled(False)
            else:
                self.easyfind_build_action.setText("Build EasyFind")
                self.easyfind_build_action.setEnabled(True)
            self.easyfind_fit_action.setVisible(canvas_ready and not building)
            self.easyfind_reset_action.setVisible(canvas_ready and not building)
            self.easyfind_fit_action.setEnabled(canvas_ready and not building)
            self.easyfind_reset_action.setEnabled(canvas_ready and not building)
        else:
            self.open_rom_toolbar_action.setVisible(True)
            self.save_session_toolbar_action.setVisible(loaded)
            self.easyfind_action.setVisible(loaded and self._current_game_code() != "")
            self.easyfind_back_action.setVisible(False)
            self.easyfind_build_action.setVisible(False)
            self.easyfind_fit_action.setVisible(False)
            self.easyfind_reset_action.setVisible(False)

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
        if not hasattr(self, "easyfind_workspace"):
            return
        if not self.assets:
            return
        self._bind_easyfind_for_current_game()
        self._in_easyfind_workspace = True
        self.workspace_stack.setCurrentWidget(self.easyfind_workspace)
        self._update_main_toolbar()
        self._refresh_easyfind_workspace()

    def return_to_browser_workspace(self) -> None:
        if hasattr(self, "workspace_stack") and hasattr(self, "browser_workspace"):
            self._save_easyfind_view_state()
            self._in_easyfind_workspace = False
            self.workspace_stack.setCurrentWidget(self.browser_workspace)
            self._update_main_toolbar()
            if self._easyfind_phase in {"loading_file", "populating", "loading_previews"}:
                self._easyfind_log(
                    "EasyFind load continuing in background. "
                    "Watch the Terminal tab for completion."
                )

    def _save_easyfind_view_state(self) -> None:
        path = self.easyfind_path
        if not path or not hasattr(self, "easyfind_workspace"):
            return
        self._easyfind_view_states[path] = (
            self.easyfind_workspace.canvas_view.capture_view_state()
        )

    def _restore_easyfind_view_state(self) -> bool:
        path = self.easyfind_path
        if not path or not hasattr(self, "easyfind_workspace"):
            return False
        state = self._easyfind_view_states.get(path)
        if not state:
            return False
        return self.easyfind_workspace.canvas_view.restore_view_state(state)

    def closeEvent(self, event) -> None:  # noqa: N802
        for attr in (
            "easyfind_worker",
            "easyfind_load_worker",
            "easyfind_populate_worker",
            "easyfind_preview_worker",
        ):
            self._stop_easyfind_worker(getattr(self, attr, None))
        super().closeEvent(event)

    def _easyfind_log(self, message: str) -> None:
        print(message, flush=True)
        self._update_status(message)

    def _set_easyfind_phase(self, phase: str, stage: str = "") -> None:
        self._easyfind_phase = phase
        self._easyfind_stage = stage

    def _clear_easyfind_worker(self, attr: str, worker: object) -> None:
        current = getattr(self, attr, None)
        if current is worker:
            setattr(self, attr, None)

    def _stop_easyfind_worker(self, worker: object | None) -> None:
        from PySide6.QtCore import QThread

        if worker is None or not isinstance(worker, QThread):
            return
        if worker.isRunning():
            worker.requestInterruption()
            if not worker.wait(60_000):
                worker.terminate()
                worker.wait(5_000)

    def _launch_easyfind_worker(self, attr: str, worker: object) -> object:
        from PySide6.QtCore import QThread

        previous = getattr(self, attr, None)
        if previous is not None and previous is not worker:
            self._stop_easyfind_worker(previous)
        if isinstance(worker, QThread):
            worker.setParent(self)
            worker.finished.connect(
                lambda w=worker: self._clear_easyfind_worker(attr, w)
            )
        setattr(self, attr, worker)
        return worker

    def _refresh_easyfind_workspace(self) -> None:
        ws = self.easyfind_workspace
        ws.inspector_panel.hide()

        if not self.assets:
            ws.show_modal_mode()
            ws.panel.show_no_assets()
            ws.panel.raise_()
            self._update_main_toolbar()
            return

        canonical = self._canonical_easyfind_path()
        if canonical is None:
            ws.show_modal_mode()
            ws.panel.show_error(
                "Could not read a Nintendo DS game code from this ROM or session.\n\n"
                "EasyFind files are stored as easyfind/<GAME_CODE>.easyfind."
            )
            ws.panel.raise_()
            self._update_main_toolbar()
            return

        if not canonical.is_file():
            ws.show_modal_mode()
            ws.panel.show_build_required(canonical)
            ws.panel.raise_()
            self._update_main_toolbar()
            return

        path_key = str(canonical)
        if (
            self._easyfind_phase == "ready"
            and self._easyfind_document is not None
            and self.easyfind_path == path_key
        ):
            self._show_easyfind_map_workspace(restore_layout=bool(self._easyfind_applied_filters))
            self._update_main_toolbar()
            return

        if self._easyfind_phase in {"loading_file", "populating", "loading_previews"}:
            ws.show_loading_mode()
            ws.panel.show_loading(stage=self._easyfind_stage or "Loading EasyFind…")
            ws.panel.raise_()
            self._update_main_toolbar()
            return

        self._start_easyfind_load(canonical)

    def _start_easyfind_load(self, path: Path) -> None:
        if self.easyfind_load_worker is not None and self.easyfind_load_worker.isRunning():
            return
        self._easyfind_job_id += 1
        self._stop_easyfind_worker(self.easyfind_populate_worker)
        self._stop_easyfind_worker(self.easyfind_cluster_worker)
        self._stop_easyfind_worker(self.easyfind_preview_worker)
        self._set_easyfind_phase("loading_file", f"Reading {path.name}…")
        self.easyfind_workspace.show_loading_mode()
        self.easyfind_workspace.panel.show_loading(stage=self._easyfind_stage)
        self.easyfind_workspace.panel.raise_()
        self.easyfind_path = str(path)
        job_id = self._easyfind_job_id
        worker = EasyFindLoadWorker(path)
        worker.finished_ok.connect(
            lambda document, validation, jid=job_id: self._on_easyfind_load_finished(
                document, validation, jid,
            )
        )
        worker.failed.connect(
            lambda message, jid=job_id: self._on_easyfind_load_failed(message, jid)
        )
        self._launch_easyfind_worker("easyfind_load_worker", worker)
        worker.start()

    def _on_easyfind_load_finished(
        self,
        document: object,
        validation: object,
        job_id: int,
    ) -> None:
        if job_id != self._easyfind_job_id:
            return
        path = Path(self.easyfind_path) if self.easyfind_path else None
        if not isinstance(validation, EasyFindValidationReport) or path is None:
            return
        if document is None or not validation.ok:
            self._set_easyfind_phase("failed", "Validation failed")
            if self._in_easyfind_workspace:
                self.easyfind_workspace.show_modal_mode()
                self.easyfind_workspace.panel.show_invalid(path, validation)
                self.easyfind_workspace.panel.raise_()
            self._easyfind_canvas_ready = False
            self._update_main_toolbar()
            self._easyfind_log(f"EasyFind load failed validation: {path.name}")
            return
        if not isinstance(document, EasyFindDocument):
            return
        self._easyfind_document = document
        self._easyfind_node_index = load_node_index(document)
        self._easyfind_applied_filters = None
        self._set_easyfind_phase("ready", "Select filters, then Apply")
        self._easyfind_canvas_ready = True
        self._easyfind_log(
            f"EasyFind index loaded: {path.name} ({len(document.nodes):,} nodes). "
            "Choose types in Grouping and click Apply to Map."
        )
        if self._in_easyfind_workspace:
            self._show_easyfind_map_workspace(restore_layout=False)
            self.easyfind_workspace.controls_panel.set_place_options(document)
        self._update_main_toolbar()

    def _on_easyfind_load_failed(self, message: str, job_id: int) -> None:
        if job_id != self._easyfind_job_id:
            return
        self._set_easyfind_phase("failed", message)
        if self._in_easyfind_workspace:
            self.easyfind_workspace.show_modal_mode()
            self.easyfind_workspace.panel.show_error(message)
            self.easyfind_workspace.panel.raise_()
        self._easyfind_canvas_ready = False
        self._update_main_toolbar()
        self._easyfind_log(f"EasyFind load failed: {message}")

    def _start_easyfind_canvas_populate(
        self,
        document: EasyFindDocument,
        job_id: int | None = None,
    ) -> None:
        if job_id is None:
            self._easyfind_job_id += 1
            job_id = self._easyfind_job_id
        self._stop_easyfind_worker(self.easyfind_populate_worker)
        self._stop_easyfind_worker(self.easyfind_cluster_worker)
        self._stop_easyfind_worker(self.easyfind_preview_worker)
        ws = self.easyfind_workspace
        filters = self._easyfind_applied_filters
        if filters is None:
            filters = ws.controls_panel.current_filters()
        self._set_easyfind_phase(
            "populating",
            "Building focus map…" if filters.filter_mode == FILTER_MODE_FOCUS else "Building cluster overview…",
        )
        if self._in_easyfind_workspace:
            ws.show_loading_mode()
            ws.panel.show_loading(stage=self._easyfind_stage)
            ws.panel.raise_()
        self._easyfind_canvas_ready = False
        self._update_main_toolbar()

        path = Path(self.easyfind_path) if self.easyfind_path else None
        if path is not None:
            ws.canvas_view.easyfind_scene.set_easyfind_path(path)

        worker = EasyFindCanvasPopulateWorker(
            document,
            filters,
            index=self._easyfind_node_index,
        )
        worker.finished_ok.connect(
            lambda layout, visible, jid=job_id: self._on_easyfind_canvas_populate_finished(
                layout, visible, jid,
            )
        )
        worker.failed.connect(
            lambda message, jid=job_id: self._on_easyfind_canvas_populate_failed(message, jid)
        )
        self._launch_easyfind_worker("easyfind_populate_worker", worker)
        worker.start()

    def _on_easyfind_canvas_populate_finished(
        self,
        layout: object,
        visible: object,
        job_id: int,
    ) -> None:
        if job_id != self._easyfind_job_id:
            return
        if self._easyfind_document is None or not isinstance(layout, EasyFindCanvasLayout):
            return
        ws = self.easyfind_workspace
        scene = ws.canvas_view.easyfind_scene
        visible_list = list(visible)
        scene.apply_layout(self._easyfind_document, layout, visible_list)
        if layout.empty:
            self._set_easyfind_phase("ready", "No matching nodes")
            if self._in_easyfind_workspace:
                ws.canvas_view.show_empty_message("No nodes match the current grouping.")
                ws.show_map_mode()
            self._easyfind_canvas_ready = True
            self._update_main_toolbar()
            self._easyfind_log("EasyFind ready (no nodes match current grouping).")
            return

        cluster_count = len(layout.clusters)
        self._set_easyfind_phase("ready", "Overview ready")
        self._easyfind_canvas_ready = True
        if self._in_easyfind_workspace:
            ws.show_map_mode()
            if not self._restore_easyfind_view_state():
                ws.canvas_view.fit_all(force=True)
            else:
                ws.canvas_view.refresh_view_lod()
        self._update_main_toolbar()
        total_nodes = sum(cluster.node_count for cluster in layout.clusters)
        if cluster_count:
            self._easyfind_log(
                f"EasyFind overview ready ({cluster_count} clusters, {total_nodes:,} assets). "
                "Click the arrow on a cluster to open it."
            )
        else:
            self._easyfind_log("EasyFind ready.")
        self._finish_easyfind_load(job_id, previews_loaded=0, previews_total=0)

    def _on_easyfind_canvas_populate_failed(self, message: str, job_id: int) -> None:
        if job_id != self._easyfind_job_id:
            return
        self._set_easyfind_phase("failed", message)
        if self._in_easyfind_workspace:
            self.easyfind_workspace.show_modal_mode()
            self.easyfind_workspace.panel.show_error(f"Could not build EasyFind canvas:\n{message}")
            self.easyfind_workspace.panel.raise_()
        self._easyfind_canvas_ready = False
        self._update_main_toolbar()
        self._easyfind_log(f"EasyFind canvas layout failed: {message}")

    def _easyfind_preview_jobs_for_scene(self, extra: object = None) -> list[tuple[str, str]]:
        jobs: list[tuple[str, str]] = []
        seen: set[str] = set()
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        document = self._easyfind_document
        if document is None:
            return jobs
        nodes_by_id = {node.node_id: node for node in document.nodes}
        for node_id in scene.node_items:
            if node_id in seen:
                continue
            node = nodes_by_id.get(node_id)
            if node is None or not node.preview_ref:
                continue
            seen.add(node_id)
            jobs.append((node_id, node.preview_ref))
        if extra is not None:
            for entry in extra:
                node = entry.node
                if node.node_id in seen or not node.preview_ref:
                    continue
                seen.add(node.node_id)
                jobs.append((node.node_id, node.preview_ref))
        return jobs

    def _start_easyfind_preview_batch(self, visible: object, job_id: int) -> None:
        path = Path(self.easyfind_path) if self.easyfind_path else None
        if path is None or not path.is_file():
            self._finish_easyfind_load(job_id, previews_loaded=0, previews_total=0)
            return
        jobs = self._easyfind_preview_jobs_for_scene(visible)
        if jobs and self._in_easyfind_workspace:
            priority_ids = self.easyfind_workspace.canvas_view.visible_preview_node_ids()
            jobs.sort(key=lambda job: (0 if job[0] in priority_ids else 1, job[0]))
        if not jobs:
            self._finish_easyfind_load(job_id, previews_loaded=0, previews_total=0)
            return

        self._easyfind_preview_total = len(jobs)
        self._stop_easyfind_worker(self.easyfind_preview_worker)
        worker = EasyFindPreviewBatchWorker(path, jobs)
        worker.batch_ready.connect(self._on_easyfind_preview_batch_ready)
        worker.finished_ok.connect(
            lambda jid=job_id: self._finish_easyfind_load(
                jid,
                previews_loaded=self._easyfind_preview_total,
                previews_total=self._easyfind_preview_total,
            )
        )
        worker.failed.connect(
            lambda message, jid=job_id: self._on_easyfind_preview_batch_failed(message, jid)
        )
        self._launch_easyfind_worker("easyfind_preview_worker", worker)
        worker.start()

    def _on_easyfind_preview_batch_ready(self, batch: object) -> None:
        if not isinstance(batch, dict):
            return
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        for node_id, png_bytes in batch.items():
            if isinstance(node_id, str) and isinstance(png_bytes, (bytes, bytearray)):
                scene.set_node_preview(node_id, bytes(png_bytes))
        if self._in_easyfind_workspace:
            self.easyfind_workspace.canvas_view.refresh_view_lod()

    def _on_easyfind_preview_batch_failed(self, message: str, job_id: int) -> None:
        if job_id != self._easyfind_job_id:
            return
        self._easyfind_log(f"EasyFind preview thumbnails stopped early: {message}")
        self._finish_easyfind_load(job_id, previews_loaded=0, previews_total=self._easyfind_preview_total)

    def _finish_easyfind_load(
        self,
        job_id: int,
        *,
        previews_loaded: int,
        previews_total: int,
    ) -> None:
        if job_id != self._easyfind_job_id:
            return
        self._set_easyfind_phase("ready", "Ready")
        node_count = len(self._easyfind_document.nodes) if self._easyfind_document else 0
        if previews_total:
            message = (
                f"EasyFind finished: {node_count:,} nodes, "
                f"{previews_loaded:,}/{previews_total:,} preview thumbnails loaded."
            )
        else:
            message = f"EasyFind finished: {node_count:,} nodes ready."
        if not self._in_easyfind_workspace:
            message += " Open EasyFind Map to view."
        self._easyfind_log(message)
        self._easyfind_canvas_ready = True
        self._update_main_toolbar()

    def _show_easyfind_empty_canvas(self, message: str) -> None:
        ws = self.easyfind_workspace
        ws.show_map_mode()
        ws.canvas_view.easyfind_scene.clear_scene()
        ws.canvas_view.show_empty_message(message)

    def _show_easyfind_map_workspace(self, *, restore_layout: bool) -> None:
        ws = self.easyfind_workspace
        if not restore_layout or self._easyfind_applied_filters is None:
            self._show_easyfind_empty_canvas(
                "Index ready. Choose types and colors in Grouping, then click Apply to Map."
            )
            return
        if ws.canvas_view.easyfind_scene.has_map_content:
            ws.show_map_mode()
            if not self._restore_easyfind_view_state():
                ws.canvas_view.fit_all()
            else:
                ws.canvas_view.refresh_view_lod()
            return
        self._easyfind_job_id += 1
        self._start_easyfind_canvas_populate(self._easyfind_document, self._easyfind_job_id)

    def _expand_easyfind_cluster(self, cluster_id: str) -> None:
        if self._easyfind_document is None or self._easyfind_applied_filters is None:
            return
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        if scene.is_cluster_expanded(cluster_id) or scene.is_cluster_portal_loading(cluster_id):
            return
        member_ids = scene.cluster_member_ids(cluster_id)
        if not member_ids:
            return
        scene.set_cluster_portal_loading(cluster_id, True)
        self._easyfind_job_id += 1
        job_id = self._easyfind_job_id
        self._stop_easyfind_worker(self.easyfind_cluster_worker)
        self._set_easyfind_phase("populating", "Opening cluster…")
        self._update_main_toolbar()

        worker = EasyFindClusterExpandWorker(
            self._easyfind_document,
            self._easyfind_applied_filters,
            cluster_id,
            member_ids,
            index=self._easyfind_node_index,
        )
        worker.finished_ok.connect(
            lambda cid, layout, visible, jid=job_id: self._on_easyfind_cluster_expand_finished(
                cid, layout, visible, jid,
            )
        )
        worker.failed.connect(
            lambda cid, message, jid=job_id: self._on_easyfind_cluster_expand_failed(
                cid, message, jid,
            )
        )
        self._launch_easyfind_worker("easyfind_cluster_worker", worker)
        worker.start()

    def _on_easyfind_cluster_expand_finished(
        self,
        cluster_id: str,
        layout: object,
        visible: object,
        job_id: int,
    ) -> None:
        if job_id != self._easyfind_job_id or self._easyfind_document is None:
            return
        if not isinstance(layout, EasyFindCanvasLayout):
            return
        visible_list = list(visible)
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        scene.apply_cluster_expansion(cluster_id, layout, visible_list)
        self.easyfind_workspace.canvas_view.refresh_view_lod()
        self._set_easyfind_phase("loading_previews", "Loading cluster previews…")
        self._easyfind_log(
            f"Opened cluster ({len(visible_list):,} tiles). Loading previews…"
        )
        self._start_easyfind_preview_batch(visible_list, job_id)

    def _on_easyfind_cluster_expand_failed(self, cluster_id: str, message: str, job_id: int) -> None:
        if job_id != self._easyfind_job_id:
            return
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        scene.set_cluster_portal_loading(cluster_id, False)
        self._set_easyfind_phase("ready", "Ready")
        self._easyfind_log(f"Could not open cluster {cluster_id}: {message}")

    def _collapse_easyfind_cluster(self, cluster_id: str) -> None:
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        if scene.collapse_cluster(cluster_id):
            self.easyfind_workspace.canvas_view.refresh_view_lod()
            self._easyfind_log(f"Collapsed cluster {cluster_id}.")

    def _apply_easyfind_canvas_filters(self) -> None:
        if self._easyfind_document is None:
            return
        filters = self.easyfind_workspace.controls_panel.current_filters()
        self._easyfind_applied_filters = filters
        if filters.filter_mode == FILTER_MODE_FOCUS and not focus_filters_are_active(filters):
            self._easyfind_log("EasyFind focus: showing all assets (no active clauses).")
        if filters.filter_mode != FILTER_MODE_FOCUS and filters.shown_types is not None and not filters.shown_types:
            self._stop_easyfind_worker(self.easyfind_populate_worker)
            self._stop_easyfind_worker(self.easyfind_cluster_worker)
            self._stop_easyfind_worker(self.easyfind_preview_worker)
            self._show_easyfind_empty_canvas(
                "Organize mode: check at least one Show type, then click Apply to Map."
            )
            self._easyfind_log("EasyFind: select at least one type to show on the map.")
            return
        self._easyfind_job_id += 1
        self._start_easyfind_canvas_populate(self._easyfind_document, self._easyfind_job_id)

    def _on_easyfind_node_selected(self, node_id: str) -> None:
        if not node_id or self._easyfind_document is None:
            self.easyfind_workspace.inspector_panel.hide()
            return
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        item = scene.node_items.get(node_id)
        if item is None:
            self.easyfind_workspace.inspector_panel.hide()
            return
        asset = None
        if item.node.asset_refs:
            asset = scene._assets_by_id.get(item.node.asset_refs[0].asset_id)
        self.easyfind_workspace.inspector_panel.show_node(
            item.node,
            asset,
            document=self._easyfind_document,
        )

    def _easyfind_filter_by_map(self, location_id: str) -> None:
        if not location_id:
            return
        ws = self.easyfind_workspace
        ws.controls_panel.tabs.setCurrentIndex(1)
        ws.controls_panel.apply_map_filter(location_id)
        self._apply_easyfind_canvas_filters()

    def _show_easyfind_node_in_browser(self, node_id: str) -> None:
        if not node_id:
            return
        scene = self.easyfind_workspace.canvas_view.easyfind_scene
        item = scene.node_items.get(node_id)
        if item is None or not item.node.asset_refs:
            QMessageBox.information(self, "EasyFind", "Could not resolve the selected node.")
            return
        asset_id = item.node.asset_refs[0].asset_id
        asset = self.assets_by_id.get(asset_id)
        if asset is None:
            QMessageBox.information(
                self,
                "EasyFind",
                "Source asset is not loaded in the current session.",
            )
            return
        self.return_to_browser_workspace()
        self._selected_asset_id = asset_id
        focused = False
        if hasattr(self, "focus_browser_on_asset"):
            focused = self.focus_browser_on_asset(asset, prefer_raw_tree=True)
        if not focused:
            for row, visible in enumerate(self.visible_assets):
                if visible.asset_id == asset_id:
                    if hasattr(self, "browser_tabs") and hasattr(self, "raw_tree"):
                        raw_index = self.browser_tabs.indexOf(self.raw_tree)
                        if raw_index >= 0:
                            self.browser_tabs.setCurrentIndex(raw_index)
                    self.on_selection_changed()
                    break
        if hasattr(self, "show_selected_details"):
            self.show_selected_details()
        if hasattr(self, "_schedule_auto_preview"):
            self._schedule_auto_preview()

    def _easyfind_reset_view(self) -> None:
        if hasattr(self, "easyfind_workspace"):
            self.easyfind_workspace.canvas_view.reset_view()
            if self.easyfind_path:
                self._easyfind_view_states.pop(self.easyfind_path, None)

    def _easyfind_fit_all(self) -> None:
        if hasattr(self, "easyfind_workspace"):
            self.easyfind_workspace.canvas_view.fit_all(force=True)
            self._save_easyfind_view_state()

    def build_easyfind_for_current_assets(self) -> None:
        if not self.assets:
            QMessageBox.information(self, "EasyFind", "Load a ROM or session before building EasyFind.")
            return

        output_path = self._canonical_easyfind_path()
        if output_path is None:
            QMessageBox.warning(self, "EasyFind", "Could not determine the Nintendo DS game code.")
            return

        if self.easyfind_worker is not None and self.easyfind_worker.isRunning():
            return

        from ..easyfind.build_dialog import EasyFindBuildDialog

        dialog = EasyFindBuildDialog(self, has_existing_file=output_path.is_file())
        from PySide6.QtWidgets import QDialog

        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        build_options = dialog.selected_options()

        from ...easyfind.build_options import BUILD_MODE_FULL

        if build_options.mode == BUILD_MODE_FULL and output_path.is_file():
            answer = QMessageBox.question(
                self,
                "Rebuild EasyFind",
                f"Replace the entire EasyFind index?\n\n{output_path}",
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

        self.easyfind_workspace.show_modal_mode()
        self.easyfind_workspace.panel.show_building(stage="Creating EasyFind document…", percent=10)
        self.easyfind_workspace.panel.raise_()
        self._easyfind_canvas_ready = False
        self._update_main_toolbar()

        web_snapshot = getattr(self, "_model_web_snapshot", None)
        if web_snapshot is not None and build_options.includes_model_previews():
            panel = self.easyfind_workspace.panel
            panel.show_model_snapshot_host()
            if not web_snapshot.begin_session(panel.model_snapshot_container()):
                panel.hide_model_snapshot_host()
                web_snapshot = None
        elif web_snapshot is not None:
            web_snapshot.end_session()

        worker = EasyFindBuildWorker(
            assets=self.assets,
            output_path=output_path,
            rom_path=self.rom_path,
            platform="nds",
            rom_title=rom_title,
            rom_game_code=rom_game_code,
            options=build_options,
            web_snapshot=web_snapshot,
        )
        worker.progress.connect(self._on_easyfind_build_progress)
        worker.finished.connect(self._on_easyfind_build_finished)
        worker.failed.connect(self._on_easyfind_build_failed)
        self._launch_easyfind_worker("easyfind_worker", worker)
        worker.start()

    def _end_model_snapshot_session(self) -> None:
        web_snapshot = getattr(self, "_model_web_snapshot", None)
        if web_snapshot is not None:
            web_snapshot.end_session()
        if hasattr(self, "easyfind_workspace"):
            self.easyfind_workspace.panel.hide_model_snapshot_host()

    def _on_easyfind_build_progress(self, message: str) -> None:
        stage_map = {
            "Creating EasyFind document…": 15,
            "Baking previews and color signatures…": 35,
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
        if message.startswith("Three.js snapshot:") or message.startswith("Rendering model thumbnail:"):
            label = message.split(":", 1)[-1].strip()
            panel = self.easyfind_workspace.panel
            if panel.model_snapshot_host.isVisible():
                panel.model_snapshot_label.setText(f"Rendering: {label}")
        self._update_status(message)

    def _on_easyfind_build_finished(self, result: object) -> None:
        self._end_model_snapshot_session()
        if not isinstance(result, EasyFindBuildResult):
            return
        self.easyfind_path = str(result.path)
        if not result.validation.ok:
            self.easyfind_workspace.show_modal_mode()
            self.easyfind_workspace.panel.show_invalid(result.path, result.validation)
            self.easyfind_workspace.panel.raise_()
            self._update_main_toolbar()
            return
        self._start_easyfind_load(result.path)
        self._update_status(f"EasyFind built successfully: {result.path}")

    def _on_easyfind_build_failed(self, message: str) -> None:
        self._end_model_snapshot_session()
        self.easyfind_workspace.show_modal_mode()
        self.easyfind_workspace.panel.show_error(message)
        self.easyfind_workspace.panel.raise_()
        self._easyfind_canvas_ready = False
        self._update_main_toolbar()
        self._update_status(f"EasyFind build failed: {message}")

    def validate_current_easyfind(self) -> None:
        path = self._canonical_easyfind_path()
        if path is None or not path.is_file():
            QMessageBox.information(self, "EasyFind", "No EasyFind file to validate.")
            return
        try:
            validation = validate_easyfind(path)
            self.easyfind_path = str(path)
            if validation.ok and self._in_easyfind_workspace:
                self._start_easyfind_load(path)
                return
            self.easyfind_workspace.show_modal_mode()
            if validation.ok:
                quick_open = load_easyfind_quick_open(path)
                self.easyfind_workspace.panel.show_validation_result(path, quick_open, validation)
            else:
                self.easyfind_workspace.panel.show_invalid(path, validation)
            self.easyfind_workspace.panel.raise_()
            self._update_status(
                f"EasyFind validation OK: {path}" if validation.ok
                else f"EasyFind validation failed: {path}"
            )
        except Exception as exc:
            self.easyfind_workspace.show_modal_mode()
            self.easyfind_workspace.panel.show_error(str(exc))
            self.easyfind_workspace.panel.raise_()
