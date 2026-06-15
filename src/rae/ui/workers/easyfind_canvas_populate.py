"""Background EasyFind canvas layout computation."""
from __future__ import annotations

from dataclasses import dataclass, replace

from PySide6.QtCore import QThread, Signal

from ...easyfind.canvas_filters import (
    FILTER_MODE_FOCUS,
    EasyFindCanvasFilters,
    focus_overview_group_by,
)
from ...easyfind.canvas_layout import build_cluster_overview_layout
from ...easyfind.models import EasyFindDocument, EasyFindNode
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
            filters = self.filters
            if self.filters.filter_mode == FILTER_MODE_FOCUS:
                filters = replace(
                    self.filters,
                    group_by=focus_overview_group_by(self.filters),
                )
            layout = build_cluster_overview_layout(
                self.document,
                filters=filters,
                index=self.index,
            )
            self.finished_ok.emit(layout, [])
        except Exception as exc:
            self.failed.emit(str(exc))
