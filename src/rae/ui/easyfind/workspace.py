"""EasyFind workspace shell widget."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .build_panel import EasyFindBuildPanel
from .grid_canvas import GridCanvas


class EasyFindWorkspace(QWidget):
    """Permanent EasyFind workspace: grid canvas with centered status modal."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.canvas = GridCanvas()
        layout.addWidget(self.canvas)

        self.build_panel = EasyFindBuildPanel(self)
        self.build_panel.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.build_panel.setGeometry(self.rect())
        if hasattr(self.build_panel, "_position_modal"):
            self.build_panel._position_modal()

    @property
    def panel(self) -> EasyFindBuildPanel:
        return self.build_panel
