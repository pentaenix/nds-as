"""EasyFind QGraphicsScene population."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QRectF
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsScene

from ...easyfind.canvas_layout import CLUSTER_TILE_GAP, CLUSTER_TILE_HEIGHT, CLUSTER_TILE_WIDTH
from ...easyfind.models import EasyFindDocument
from .canvas_items import EasyFindClusterPortalItem, EasyFindGroupRegionItem, EasyFindNodeItem
from .canvas_lod import lod_for_zoom
from .layout import EasyFindCanvasFilters, EasyFindCanvasLayout, build_source_level_canvas_layout

_ITEM_BATCH = 300


class EasyFindCanvasScene(QGraphicsScene):
    def __init__(self) -> None:
        super().__init__()
        self._document: EasyFindDocument | None = None
        self._layout: EasyFindCanvasLayout | None = None
        self._node_items: dict[str, EasyFindNodeItem] = {}
        self._assets_by_id: dict[str, object] = {}
        self._easyfind_path: Path | None = None
        self._preview_bytes: dict[str, bytes] = {}
        self._visible_node_ids: set[str] = set()
        self._current_lod = lod_for_zoom(0.35)
        self._cluster_portals: dict[str, EasyFindClusterPortalItem] = {}
        self._cluster_members: dict[str, frozenset[str]] = {}
        self._expanded_clusters: set[str] = set()
        self._cluster_origin: dict[str, tuple[float, float]] = {}
        self._cluster_expanded_bounds: dict[str, tuple[float, float]] = {}
        self._portal_home: dict[str, tuple[float, float]] = {}

    @property
    def node_items(self) -> dict[str, EasyFindNodeItem]:
        return self._node_items

    @property
    def layout(self) -> EasyFindCanvasLayout | None:
        return self._layout

    def set_easyfind_path(self, path: Path | None) -> None:
        self._easyfind_path = path

    def clear_scene(self) -> None:
        self.clear()
        self._node_items.clear()
        self._layout = None
        self._preview_bytes.clear()
        self._visible_node_ids.clear()
        self._cluster_portals.clear()
        self._cluster_members.clear()
        self._expanded_clusters.clear()
        self._cluster_origin.clear()
        self._cluster_expanded_bounds.clear()
        self._portal_home.clear()

    @property
    def has_map_content(self) -> bool:
        return bool(self._node_items or self._cluster_portals)

    def cluster_member_ids(self, cluster_id: str) -> frozenset[str]:
        return self._cluster_members.get(cluster_id, frozenset())

    def is_cluster_expanded(self, cluster_id: str) -> bool:
        return cluster_id in self._expanded_clusters

    def apply_cluster_overview(
        self,
        document: EasyFindDocument,
        layout: EasyFindCanvasLayout,
    ) -> None:
        """Show coarse cluster portals only; detail is loaded on demand."""
        self.clear_scene()
        self._document = document
        self._layout = layout
        self._assets_by_id = {asset.asset_id: asset for asset in document.assets}

        for cluster in layout.clusters:
            portal = EasyFindClusterPortalItem(
                cluster.cluster_id,
                cluster.title,
                cluster.accent_rgb,
                cluster.width,
                cluster.height,
                cluster.node_count,
            )
            portal.setPos(cluster.x, cluster.y)
            self.addItem(portal)
            self._cluster_portals[cluster.cluster_id] = portal
            self._cluster_members[cluster.cluster_id] = frozenset(cluster.node_ids)
            self._portal_home[cluster.cluster_id] = (cluster.x, cluster.y)

        if not layout.empty:
            self.setSceneRect(0, 0, layout.bounds.width, layout.bounds.height)

    def apply_cluster_expansion(
        self,
        cluster_id: str,
        layout: EasyFindCanvasLayout,
        visible_nodes: list,
    ) -> None:
        """Materialize one opened cluster at its portal position."""
        if self._document is None or cluster_id in self._expanded_clusters:
            return

        portal = self._cluster_portals.pop(cluster_id, None)
        origin_x = portal.pos().x() if portal is not None else 0.0
        origin_y = portal.pos().y() if portal is not None else 0.0
        if portal is not None:
            self.removeItem(portal)

        self._expanded_clusters.add(cluster_id)
        self._cluster_origin[cluster_id] = (origin_x, origin_y)
        self._cluster_expanded_bounds[cluster_id] = (layout.bounds.width, layout.bounds.height)
        app = QApplication.instance()

        for group in layout.groups:
            item = EasyFindGroupRegionItem(
                group.group_id,
                group.title,
                group.level,
                group.accent_rgb,
                group.width,
                group.height,
            )
            item.setPos(origin_x + group.x, origin_y + group.y)
            item.setData(0, cluster_id)
            self.addItem(item)

        for index, entry in enumerate(visible_nodes):
            node = entry.node
            node_item = EasyFindNodeItem(node)
            node_item.setPos(origin_x + entry.x, origin_y + entry.y)
            node_item.setData(0, cluster_id)
            self.addItem(node_item)
            self._node_items[node.node_id] = node_item
            if app is not None and index > 0 and index % _ITEM_BATCH == 0:
                app.processEvents()

        self._grow_scene_rect(origin_x, origin_y, layout.bounds.width, layout.bounds.height)
        self._relayout_cluster_portals()

    def collapse_cluster(self, cluster_id: str) -> bool:
        """Remove a materialized cluster and restore its portal tile."""
        if cluster_id not in self._expanded_clusters or self._layout is None:
            return False

        to_remove: list = []
        for item in self.items():
            if item.data(0) == cluster_id:
                to_remove.append(item)

        for item in to_remove:
            if isinstance(item, EasyFindNodeItem):
                self._node_items.pop(item.node.node_id, None)
                self._preview_bytes.pop(item.node.node_id, None)
            self.removeItem(item)

        self._expanded_clusters.discard(cluster_id)
        self._cluster_origin.pop(cluster_id, None)
        self._cluster_expanded_bounds.pop(cluster_id, None)

        cluster = next(
            (c for c in self._layout.clusters if c.cluster_id == cluster_id),
            None,
        )
        if cluster is None:
            return True

        portal = EasyFindClusterPortalItem(
            cluster.cluster_id,
            cluster.title,
            cluster.accent_rgb,
            cluster.width,
            cluster.height,
            cluster.node_count,
        )
        home = self._portal_home.get(cluster.cluster_id, (cluster.x, cluster.y))
        portal.setPos(home[0], home[1])
        self.addItem(portal)
        self._cluster_portals[cluster.cluster_id] = portal
        self._relayout_cluster_portals()
        return True

    def _expanded_blockers(self, *, margin: float) -> list[QRectF]:
        blockers: list[QRectF] = []
        for cluster_id in self._expanded_clusters:
            origin = self._cluster_origin.get(cluster_id, (0.0, 0.0))
            bounds = self._cluster_expanded_bounds.get(cluster_id)
            if bounds is None:
                continue
            width, height = bounds
            blockers.append(QRectF(origin[0], origin[1], width, height).adjusted(
                -margin, -margin, margin, margin,
            ))
        return blockers

    def _place_portal_clear_of(
        self,
        home_x: float,
        home_y: float,
        blockers: list[QRectF],
        *,
        margin: float,
    ) -> tuple[float, float]:
        rect = QRectF(home_x, home_y, CLUSTER_TILE_WIDTH, CLUSTER_TILE_HEIGHT)
        for _ in range(64):
            hit = next((block for block in blockers if rect.intersects(block)), None)
            if hit is None:
                return rect.x(), rect.y()
            rect.moveTo(hit.right() + margin, rect.y())
        if blockers:
            return home_x, max(block.bottom() for block in blockers) + margin
        return home_x, home_y

    def _relayout_cluster_portals(self) -> None:
        """Shift unopened color portals away from expanded cluster content."""
        if self._layout is None or not self._cluster_portals:
            return

        margin = CLUSTER_TILE_GAP
        blockers = self._expanded_blockers(margin=margin)
        placed: list[QRectF] = []

        clusters = sorted(
            self._layout.clusters,
            key=lambda cluster: (
                self._portal_home.get(cluster.cluster_id, (cluster.x, cluster.y))[1],
                self._portal_home.get(cluster.cluster_id, (cluster.x, cluster.y))[0],
            ),
        )
        for cluster in clusters:
            if cluster.cluster_id in self._expanded_clusters:
                continue
            portal = self._cluster_portals.get(cluster.cluster_id)
            if portal is None:
                continue
            home = self._portal_home.get(cluster.cluster_id, (cluster.x, cluster.y))
            x, y = self._place_portal_clear_of(home[0], home[1], blockers + placed, margin=margin)
            portal.setPos(x, y)
            placed.append(QRectF(x, y, CLUSTER_TILE_WIDTH, CLUSTER_TILE_HEIGHT))

    def _grow_scene_rect(self, origin_x: float, origin_y: float, width: float, height: float) -> None:
        rect = self.sceneRect()
        right = origin_x + width
        bottom = origin_y + height
        new_w = max(rect.width(), right)
        new_h = max(rect.height(), bottom)
        if new_w > rect.width() or new_h > rect.height():
            self.setSceneRect(0, 0, new_w, new_h)

    def apply_layout(
        self,
        document: EasyFindDocument,
        layout: EasyFindCanvasLayout,
        visible_nodes: list,
    ) -> None:
        """Create group and node items on the UI thread without reading preview blobs."""
        if layout.clusters and not visible_nodes:
            self.apply_cluster_overview(document, layout)
            return
        self.clear_scene()
        self._document = document
        self._assets_by_id = {asset.asset_id: asset for asset in document.assets}
        self._layout = layout
        app = QApplication.instance()

        for group in layout.groups:
            item = EasyFindGroupRegionItem(
                group.group_id,
                group.title,
                group.level,
                group.accent_rgb,
                group.width,
                group.height,
            )
            item.setPos(group.x, group.y)
            self.addItem(item)

        for index, entry in enumerate(visible_nodes):
            node = entry.node
            node_item = EasyFindNodeItem(node)
            node_item.setPos(entry.x, entry.y)
            self.addItem(node_item)
            self._node_items[node.node_id] = node_item
            if app is not None and index > 0 and index % _ITEM_BATCH == 0:
                app.processEvents()

    def set_node_preview(self, node_id: str, png_bytes: bytes) -> None:
        self._preview_bytes[node_id] = png_bytes
        if node_id in self._visible_node_ids and self._current_lod > 0:
            self._apply_preview_to_item(node_id)

    def sync_view_state(self, zoom: float, view_rect) -> set[str]:
        """Update LOD tiers and decode previews only for tiles in view."""
        lod = lod_for_zoom(zoom)
        visible: set[str] = set()
        for node_id, item in self._node_items.items():
            if item.sceneBoundingRect().intersects(view_rect):
                visible.add(node_id)

        lod_changed = lod != self._current_lod
        previous_visible = self._visible_node_ids
        visibility_changed = visible != previous_visible
        self._current_lod = lod
        self._visible_node_ids = visible

        if lod_changed:
            for item in self._node_items.values():
                item.set_display_lod(lod)

        if lod <= 0:
            if lod_changed or visibility_changed:
                for node_id in visible:
                    item = self._node_items.get(node_id)
                    if item is not None:
                        item.clear_preview()
            return visible

        if visibility_changed or lod_changed:
            for node_id in visible:
                self._apply_preview_to_item(node_id)
            if visibility_changed:
                for node_id in previous_visible - visible:
                    item = self._node_items.get(node_id)
                    if item is not None:
                        item.clear_preview()
        return visible

    def _apply_preview_to_item(self, node_id: str) -> None:
        item = self._node_items.get(node_id)
        png_bytes = self._preview_bytes.get(node_id)
        if item is None or png_bytes is None:
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(png_bytes, "PNG"):
            return
        item.set_preview_source(pixmap)
        item.set_display_lod(self._current_lod)

    def populate(
        self,
        document: EasyFindDocument,
        filters: EasyFindCanvasFilters,
        *,
        easyfind_path: Path | None = None,
    ) -> EasyFindCanvasLayout:
        """Synchronous fallback used by tests; UI should prefer async populate workers."""
        if easyfind_path is not None:
            self._easyfind_path = easyfind_path
        layout = build_source_level_canvas_layout(document, filters=filters)
        visible = []
        for node in document.nodes:
            placement = layout.nodes.get(node.node_id)
            if placement is None:
                continue
            from ..workers.easyfind_canvas_populate import EasyFindVisibleNode

            visible.append(EasyFindVisibleNode(
                node=node,
                x=placement.x,
                y=placement.y,
            ))
        self.apply_layout(document, layout, visible)
        return layout

    def select_node(self, node_id: str | None) -> EasyFindNodeItem | None:
        selected_item = None
        for item_id, item in self._node_items.items():
            is_selected = item_id == node_id
            item.set_selected_state(is_selected)
            if is_selected:
                selected_item = item
        return selected_item
