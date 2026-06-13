"""Floating EasyFind node inspector."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ...easyfind.models import EasyFindAssetRef, EasyFindNode
from ...util import human_size
from ..constants import CHROME_BUTTON_STYLE

PANEL_STYLE = """
QFrame#easyfindInspector {
    background-color: rgba(38, 38, 38, 235);
    border: 1px solid #4a4a4a;
    border-radius: 8px;
}
"""


class EasyFindInspectorPanel(QFrame):
    show_in_browser_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("easyfindInspector")
        self.setStyleSheet(PANEL_STYLE)
        self._node_id: str | None = None
        self._build_ui()
        self.hide()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        self.title_label = QLabel()
        self.title_label.setStyleSheet("font-size: 14px; font-weight: 600; color: #f2f2f2;")
        self.body_label = QLabel()
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet("font-size: 12px; color: #c8c8c8;")
        self.show_button = QPushButton("Show in Browser")
        self.show_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.show_button.clicked.connect(self._on_show)
        layout.addWidget(self.title_label)
        layout.addWidget(self.body_label)
        layout.addWidget(self.show_button)
        self.setFixedWidth(320)

    def _on_show(self) -> None:
        if self._node_id:
            self.show_in_browser_requested.emit(self._node_id)

    def show_node(self, node: EasyFindNode | None, asset: EasyFindAssetRef | None) -> None:
        if node is None:
            self._node_id = None
            self.hide()
            return
        self._node_id = node.node_id
        magic = asset.magic if asset is not None else str(node.metadata.get("magic", "?"))
        kind = node.node_kind.replace("_", " ")
        self.title_label.setText(f"{magic} {kind.title()}")
        lines = [f"Path:\n  {asset.virtual_path if asset else node.label}"]
        lines.append(f"Kind:\n  {node.node_kind}")
        lines.append(f"Magic:\n  {magic}")
        if asset is not None:
            lines.append(f"Size:\n  {human_size(asset.size)}")
            mapping = asset.mapping_label or asset.mapping_category or "—"
            confidence = asset.mapping_confidence or "—"
            lines.append(f"Mapping:\n  {mapping} / {confidence}")
            lines.append(f"Asset ID:\n  {asset.asset_id}")
        self.body_label.setText("\n\n".join(lines))
        self.show()
