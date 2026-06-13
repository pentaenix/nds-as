"""Background EasyFind canvas layout computation."""
from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QThread, Signal

from ...easyfind.canvas_filters import EasyFindCanvasFilters
from ...easyfind.canvas_layout import build_cluster_overview_layout
from ...easyfind.models import EasyFindDocument
from ...easyfind.node_index import EasyFindNodeIndex


@dataclass(frozen=True)
class EasyFindVisibleNode:
    node: EasyFindNode
    x: float
    y: float


class EasyFindCanvasPopulateWorker(QThread):
    finished_ok = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        document: EasyFindDocument,
        filters: EasyFindCanvasFilters,
        *,
        index: EasyFindNodeIndex | None = None,
    ) -> None:
        super().__init__()
        self.document = document
        self.filters = filters
        self.index = index

    def run(self) -> None:
        try:
            layout = build_cluster_overview_layout(
                self.document,
                filters=self.filters,
                index=self.index,
            )
            self.finished_ok.emit(layout, [])
        except Exception as exc:
            self.failed.emit(str(exc))
