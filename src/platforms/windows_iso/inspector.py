"""Island-owned animation controls for Windows ISO GLB previews."""
from __future__ import annotations

import json
from pathlib import Path
import struct
from typing import Callable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


def _animation_names(glb_path: Path) -> list[str]:
    payload = Path(glb_path).read_bytes()
    if len(payload) < 20 or payload[:4] != b"glTF":
        return []
    json_size, kind = struct.unpack_from("<I4s", payload, 12)
    if kind != b"JSON" or 20 + json_size > len(payload):
        return []
    document = json.loads(payload[20:20 + json_size].rstrip(b" \0"))
    return [str(row.get("name") or f"animation_{index}")
            for index, row in enumerate(document.get("animations") or [])]


class WindowsIsoAnimationsWidget(QWidget):
    play_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        self.summary = QLabel("No animation clips")
        self.list = QListWidget()
        self.list.setToolTip("Double-click a Marine Park Empire animation to play it.")
        buttons = QHBoxLayout()
        play = QPushButton("Play selected")
        stop = QPushButton("Stop")
        self.load_all = QPushButton("Load all animations")
        buttons.addWidget(play)
        buttons.addWidget(stop)
        buttons.addWidget(self.load_all)
        buttons.addStretch(1)
        layout.addWidget(self.summary)
        layout.addWidget(self.list, 1)
        layout.addLayout(buttons)
        play.clicked.connect(self._play)
        stop.clicked.connect(self.stop_requested.emit)
        self.load_all.clicked.connect(self._request_all)
        self.list.itemDoubleClicked.connect(lambda _item: self._play())
        self._on_load_all: Callable[[], None] | None = None

    def set_animations(
        self,
        names: list[str],
        *,
        complete: bool,
        on_load_all: Callable[[], None] | None,
    ) -> None:
        self.list.clear()
        self.list.addItems(names)
        suffix = "complete model" if complete else "fast preview subset"
        self.summary.setText(f"{len(names):,} animation clip(s) — {suffix}")
        self._on_load_all = on_load_all
        self.load_all.setVisible(not complete)
        self.load_all.setEnabled(on_load_all is not None)
        if names:
            self.list.setCurrentRow(0)

    def _play(self) -> None:
        item = self.list.currentItem()
        if item is not None:
            self.play_requested.emit(item.text())

    def _request_all(self) -> None:
        if self._on_load_all is not None:
            self.load_all.setEnabled(False)
            self._on_load_all()


def register_inspector(
    window: object,
    *,
    glb_path: Path,
    asset_id: str,
    complete: bool = False,
    on_load_all: Callable[[], None] | None = None,
) -> None:
    """Create/reuse the platform tab and populate it from the generated GLB."""
    tabs = getattr(window, "preview_inspector_tabs", None)
    preview = getattr(window, "preview", None)
    if tabs is None or preview is None:
        return
    widget = getattr(window, "_windows_iso_animations_widget", None)
    if widget is None:
        widget = WindowsIsoAnimationsWidget(tabs)
        widget.play_requested.connect(
            lambda name: getattr(preview, "play_glb_animation", lambda _name: None)(name)
        )
        widget.stop_requested.connect(
            lambda: getattr(preview, "stop_glb_animation", lambda: None)()
        )
        tab_index = tabs.addTab(widget, "Model Animations")
        setattr(window, "_windows_iso_animations_widget", widget)
        setattr(window, "_windows_iso_animations_tab", tab_index)
    names = _animation_names(glb_path)
    widget.set_animations(
        names, complete=complete, on_load_all=on_load_all,
    )
    tab_index = int(getattr(window, "_windows_iso_animations_tab"))
    tabs.setTabVisible(tab_index, bool(names))
    setattr(window, "_windows_iso_inspector_asset_id", asset_id)
