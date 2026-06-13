"""EasyFind pan/zoom canvas view."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QWheelEvent
from PySide6.QtWidgets import QGraphicsView, QMenu

from .canvas_lod import LOD_ZOOM_LABELS_ONLY
from .canvas_scene import EasyFindCanvasScene

CANVAS_BG = QColor(42, 42, 42)
MINOR_GRID = QColor(56, 56, 56)
MAJOR_GRID = QColor(74, 74, 74)
MINOR_SPACING = 20
MAJOR_EVERY = 5
MIN_ZOOM = 0.02
MAX_ZOOM = 2.5
DEFAULT_ZOOM = 0.35


class EasyFindCanvasView(QGraphicsView):
    node_selected = Signal(str)
    node_activated = Signal(str)
    show_in_browser_requested = Signal(str)
    cluster_expand_requested = Signal(str)
    cluster_collapse_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = EasyFindCanvasScene()
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(CANVAS_BG)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._panning = False
        self._space_pan = False
        self._last_pan_pos = QPointF()
        self._selected_node_id: str | None = None
        self._has_initial_fit = False
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self._lod_refresh_timer = QTimer(self)
        self._lod_refresh_timer.setSingleShot(True)
        self._lod_refresh_timer.setInterval(120)
        self._lod_refresh_timer.timeout.connect(self.refresh_view_lod)

    @property
    def easyfind_scene(self) -> EasyFindCanvasScene:
        return self._scene

    def _node_item_at(self, point: QPoint):
        from .canvas_items import EasyFindNodeItem

        for item in self.items(point):
            if isinstance(item, EasyFindNodeItem):
                return item
        return None

    def _cluster_portal_at(self, point: QPoint):
        from .canvas_items import EasyFindClusterPortalItem

        for item in self.items(point):
            if isinstance(item, EasyFindClusterPortalItem):
                return item
        return None

    def _cluster_group_at(self, point: QPoint) -> str | None:
        from .canvas_items import EasyFindGroupRegionItem

        for item in self.items(point):
            if isinstance(item, EasyFindGroupRegionItem):
                cluster_id = item.data(0)
                if cluster_id:
                    return str(cluster_id)
        return None

    def capture_view_state(self) -> dict[str, float]:
        center = self.mapToScene(self.viewport().rect().center())
        return {
            "zoom": float(self.transform().m11()),
            "center_x": float(center.x()),
            "center_y": float(center.y()),
        }

    def restore_view_state(self, state: dict[str, float] | None) -> bool:
        if not state:
            return False
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, float(state.get("zoom", DEFAULT_ZOOM))))
        self.resetTransform()
        self.scale(zoom, zoom)
        self.centerOn(QPointF(state["center_x"], state["center_y"]))
        self.refresh_view_lod()
        return True

    def _view_scene_rect(self, *, prefetch_margin_px: float = 240.0):
        viewport_rect = self.viewport().rect()
        scene_rect = self.mapToScene(viewport_rect).boundingRect()
        zoom = max(self.transform().m11(), 0.01)
        margin = prefetch_margin_px / zoom
        return scene_rect.adjusted(-margin, -margin, margin, margin)

    def refresh_view_lod(self) -> set[str]:
        return self._scene.sync_view_state(self.transform().m11(), self._view_scene_rect())

    def visible_preview_node_ids(self) -> set[str]:
        return self._scene.sync_view_state(self.transform().m11(), self._view_scene_rect())

    def _schedule_lod_refresh(self) -> None:
        self._lod_refresh_timer.start()

    def drawBackground(self, painter: QPainter, rect) -> None:  # noqa: N802
        painter.fillRect(rect, CANVAS_BG)
        left = int(rect.left()) - (int(rect.left()) % MINOR_SPACING)
        top = int(rect.top()) - (int(rect.top()) % MINOR_SPACING)
        right = int(rect.right())
        bottom = int(rect.bottom())
        major_step = MINOR_SPACING * MAJOR_EVERY

        minor_pen = QPen(MINOR_GRID)
        painter.setPen(minor_pen)
        x = left
        while x <= right:
            if x % major_step != 0:
                painter.drawLine(x, top, x, bottom)
            x += MINOR_SPACING
        y = top
        while y <= bottom:
            if y % major_step != 0:
                painter.drawLine(left, y, right, y)
            y += MINOR_SPACING

        major_pen = QPen(MAJOR_GRID)
        painter.setPen(major_pen)
        x = left - (left % major_step)
        while x <= right:
            painter.drawLine(x, top, x, bottom)
            x += major_step
        y = top - (top % major_step)
        while y <= bottom:
            painter.drawLine(left, y, right, y)
            y += major_step

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        if event.angleDelta().y() == 0:
            return
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._apply_zoom(factor)
        self._schedule_lod_refresh()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._space_pan = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self._selected_node_id:
            self.show_in_browser_requested.emit(self._selected_node_id)
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._space_pan = False
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        from .canvas_items import EasyFindNodeItem

        point = event.position().toPoint()
        node_item = self._node_item_at(point)
        if event.button() in {
            Qt.MouseButton.MiddleButton,
            Qt.MouseButton.RightButton,
        } or (self._space_pan and event.button() == Qt.MouseButton.LeftButton):
            self._panning = True
            self._last_pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and not isinstance(node_item, EasyFindNodeItem):
            self._panning = True
            self._last_pan_pos = event.position()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.NoViewportUpdate)
            self._selected_node_id = None
            self._scene.select_node(None)
            self.node_selected.emit("")
            event.accept()
            return
        super().mousePressEvent(event)
        if node_item is None:
            self._selected_node_id = None
            self._scene.select_node(None)
            self.node_selected.emit("")
            return
        self._selected_node_id = node_item.node.node_id
        self._scene.select_node(node_item.node.node_id)
        self.node_selected.emit(node_item.node.node_id)

    def _pan_viewport_by_pixels(self, delta: QPointF) -> None:
        """Pan in scene space — scroll bars are hidden so centerOn is used instead."""
        zoom = max(float(self.transform().m11()), 1e-6)
        scene_delta = QPointF(delta.x() / zoom, delta.y() / zoom)
        center = self.mapToScene(self.viewport().rect().center())
        self.centerOn(center.x() - scene_delta.x(), center.y() - scene_delta.y())

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            delta = event.position() - self._last_pan_pos
            self._last_pan_pos = event.position()
            self._pan_viewport_by_pixels(delta)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if self._space_pan else Qt.CursorShape.ArrowCursor)
            self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
            self.viewport().update()
            self._schedule_lod_refresh()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        point = event.position().toPoint()
        node_item = self._node_item_at(point)
        if node_item is not None:
            self.node_activated.emit(node_item.node.node_id)
            self.show_in_browser_requested.emit(node_item.node.node_id)
            event.accept()
            return
        portal = self._cluster_portal_at(point)
        if portal is not None:
            self.cluster_expand_requested.emit(portal.cluster_id)
            event.accept()
            return
        cluster_id = self._cluster_group_at(point)
        if cluster_id is not None and self._scene.is_cluster_expanded(cluster_id):
            self.cluster_collapse_requested.emit(cluster_id)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:  # noqa: N802
        node_item = self._node_item_at(event.pos())
        if node_item is None:
            return
        node_id = node_item.node.node_id
        asset_id = node_item.node.asset_refs[0].asset_id if node_item.node.asset_refs else ""
        menu = QMenu(self)
        show_action = menu.addAction("Show in Browser")
        copy_path = menu.addAction("Copy Virtual Path")
        copy_id = menu.addAction("Copy Asset ID")
        chosen = menu.exec(event.globalPos())
        if chosen is show_action:
            self.show_in_browser_requested.emit(node_id)
        elif chosen is copy_path:
            from PySide6.QtGui import QGuiApplication
            asset = self._scene._assets_by_id.get(asset_id)
            path = getattr(asset, "virtual_path", node_item.node.label)
            QGuiApplication.clipboard().setText(str(path))
        elif chosen is copy_id:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(asset_id)

    def _apply_zoom(self, factor: float) -> None:
        current = self.transform().m11()
        target = max(MIN_ZOOM, min(MAX_ZOOM, current * factor))
        self.resetTransform()
        self.scale(target, target)

    def scrollContentsBy(self, dx: int, dy: int) -> None:  # noqa: N802
        super().scrollContentsBy(dx, dy)
        if self._panning:
            return
        self._schedule_lod_refresh()

    def reset_view(self) -> None:
        self.resetTransform()
        self.scale(DEFAULT_ZOOM, DEFAULT_ZOOM)
        self.centerOn(0, 0)
        self._has_initial_fit = False
        self.refresh_view_lod()

    def fit_all(self, *, force: bool = False) -> None:
        if self._has_initial_fit and not force:
            return
        layout = self._scene.layout
        if layout is None:
            return
        bounds = layout.bounds
        rect = self._scene.sceneRect()
        if rect.isNull():
            self._scene.setSceneRect(0, 0, bounds.width, bounds.height)
        else:
            self._scene.setSceneRect(
                0, 0, max(bounds.width, 1.0), max(bounds.height, 1.0)
            )
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        zoom = float(self.transform().m11())
        if zoom < LOD_ZOOM_LABELS_ONLY:
            self.resetTransform()
            self.scale(LOD_ZOOM_LABELS_ONLY, LOD_ZOOM_LABELS_ONLY)
        self._has_initial_fit = True
        self.refresh_view_lod()

    def show_empty_message(self, message: str) -> None:
        self._scene.clear_scene()
        self._scene.addText(message)
