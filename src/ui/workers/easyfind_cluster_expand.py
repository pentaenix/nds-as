"""Background layout for one opened EasyFind cluster."""
from __future__ import annotations

from PySide6.QtCore import QThread, Signal

from ...easyfind.canvas_filters import EasyFindCanvasFilters
from ...easyfind.canvas_layout import build_cluster_detail_layout
from ...easyfind.models import EasyFindDocument
from ...easyfind.node_index import EasyFindNodeIndex
from .easyfind_canvas_populate import EasyFindVisibleNode


class EasyFindClusterExpandWorker(QThread):
    finished_ok = Signal(str, object, object)
    failed = Signal(str, str)

    def __init__(
        self,
        document: EasyFindDocument,
        filters: EasyFindCanvasFilters,
        cluster_id: str,
        member_ids: frozenset[str],
        *,
        index: EasyFindNodeIndex | None = None,
    ) -> None:
        super().__init__()
        self.document = document
        self.filters = filters
        self.cluster_id = cluster_id
        self.member_ids = member_ids
        self.index = index

    def run(self) -> None:
        try:
            layout = build_cluster_detail_layout(
                self.document,
                filters=self.filters,
                cluster_id=self.cluster_id,
                member_ids=self.member_ids,
                index=self.index,
            )
            visible: list[EasyFindVisibleNode] = []
            for node in self.document.nodes:
                placement = layout.nodes.get(node.node_id)
                if placement is None:
                    continue
                visible.append(EasyFindVisibleNode(
                    node=node,
                    x=placement.x,
                    y=placement.y,
                ))
            self.finished_ok.emit(self.cluster_id, layout, visible)
        except Exception as exc:
            self.failed.emit(self.cluster_id, str(exc))
