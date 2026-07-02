"""EasyFind workspace shell widget."""
from __future__ import annotations

from PySide6.QtWidgets import QVBoxLayout, QWidget

from .build_panel import EasyFindBuildPanel
from .canvas import EasyFindCanvasView
from .controls_panel import EasyFindControlsPanel
from .inspector import EasyFindInspectorPanel


class EasyFindWorkspace(QWidget):
    """EasyFind workspace: map canvas, overlays, and modal states."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.canvas_view = EasyFindCanvasView()
        layout.addWidget(self.canvas_view)

        self.build_panel = EasyFindBuildPanel(self)
        self.controls_panel = EasyFindControlsPanel(self)
        self.inspector_panel = EasyFindInspectorPanel(self)

        self.controls_panel.hide()
        self.inspector_panel.hide()
        self.build_panel.raise_()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        rect = self.rect()
        self.build_panel.setGeometry(rect)
        if hasattr(self.build_panel, "_position_modal"):
            self.build_panel._position_modal()

        margin = 16
        self.controls_panel.adjustSize()
        self.controls_panel.move(
            max(margin, rect.width() - self.controls_panel.width() - margin),
            margin,
        )
        self.inspector_panel.adjustSize()
        self.inspector_panel.move(
            margin,
            max(margin, rect.height() - self.inspector_panel.height() - margin),
        )

    def show_map_mode(self) -> None:
        self.canvas_view.show()
        self.controls_panel.show()
        self.build_panel.hide()

    def show_modal_mode(self) -> None:
        self.controls_panel.hide()
        self.inspector_panel.hide()
        self.build_panel.show()
        self.build_panel.raise_()

    def show_loading_mode(self) -> None:
        self.show_modal_mode()
        self.build_panel.show_loading()

    @property
    def panel(self) -> EasyFindBuildPanel:
        return self.build_panel
