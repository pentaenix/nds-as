"""Floating EasyFind node inspector."""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from ...easyfind.models import EasyFindAssetRef, EasyFindDocument, EasyFindLocation, EasyFindNode
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
    filter_map_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("easyfindInspector")
        self.setStyleSheet(PANEL_STYLE)
        self._node_id: str | None = None
        self._usage_buttons: list[QPushButton] = []
        self._usage_container: QWidget | None = None
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
        self._usage_container = QWidget()
        self._usage_layout = QVBoxLayout(self._usage_container)
        self._usage_layout.setContentsMargins(0, 0, 0, 0)
        self._usage_layout.setSpacing(4)
        self.show_button = QPushButton("Show in Browser")
        self.show_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.show_button.clicked.connect(self._on_show)
        layout.addWidget(self.title_label)
        layout.addWidget(self.body_label)
        layout.addWidget(self._usage_container)
        layout.addWidget(self.show_button)
        self.setFixedWidth(320)

    def _clear_usage_buttons(self) -> None:
        for button in self._usage_buttons:
            button.deleteLater()
        self._usage_buttons.clear()
        while self._usage_layout.count():
            item = self._usage_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _on_show(self) -> None:
        if self._node_id:
            self.show_in_browser_requested.emit(self._node_id)

    def _format_usage_label(self, location: EasyFindLocation, *, confidence: str) -> str:
        meta = location.metadata or {}
        map_index = meta.get("map_index")
        suffix = f" (Map #{map_index})" if map_index is not None else ""
        label = f"{location.name}{suffix}"
        if confidence == "heuristic":
            label += " · heuristic"
        return label

    def show_node(
        self,
        node: EasyFindNode | None,
        asset: EasyFindAssetRef | None,
        *,
        document: EasyFindDocument | None = None,
    ) -> None:
        if node is None:
            self._node_id = None
            self._clear_usage_buttons()
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

        self._clear_usage_buttons()
        usage_lines: list[str] = []
        if document is not None:
            locations_by_id = {loc.location_id: loc for loc in document.locations}
            tagged = [
                tag for tag in document.asset_tags
                if tag.node_id == node.node_id and tag.location_id
            ]
            tagged.sort(
                key=lambda tag: (
                    locations_by_id.get(str(tag.location_id)).group if locations_by_id.get(str(tag.location_id)) else "Unknown",
                    locations_by_id.get(str(tag.location_id)).name if locations_by_id.get(str(tag.location_id)) else str(tag.location_id),
                )
            )
            if tagged:
                usage_lines.append("Used in:")
                for tag in tagged:
                    loc = locations_by_id.get(str(tag.location_id))
                    if loc is None:
                        continue
                    conf = str((tag.metadata or {}).get("confidence", ""))
                    usage_lines.append(f"  • {self._format_usage_label(loc, confidence=conf)}")
                    btn = QPushButton(f"Filter: {loc.name}")
                    btn.setStyleSheet(CHROME_BUTTON_STYLE)
                    location_id = str(tag.location_id)
                    btn.clicked.connect(lambda _checked=False, lid=location_id: self.filter_map_requested.emit(lid))
                    self._usage_layout.addWidget(btn)
                    self._usage_buttons.append(btn)
            else:
                usage_lines.append("Used in:\n  Not linked to any map")

        if usage_lines:
            lines.extend(usage_lines)

        self.body_label.setText("\n\n".join(lines))
        self._usage_container.setVisible(bool(self._usage_buttons or usage_lines))
        self.show()
