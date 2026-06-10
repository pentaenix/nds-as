"""Animation state editor: custom frame groups, speed, loop, preview."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.texture_sequences import (
    DEFAULT_FRAME_DURATION_MS,
    DEFAULT_PLAY_STATE,
    is_numbered_frame_key,
    normalize_material_spec,
    state_names,
)


class AnimationStatesWidget(QWidget):
    preview_state_changed = Signal(str, str)
    spec_changed = Signal(str, dict)

    def __init__(self) -> None:
        super().__init__()
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.MinimumExpanding)
        self._asset_id: str | None = None
        self._materials: dict[str, dict] = {}
        self._current_material: str | None = None
        self._building = False

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(6)

        hint = QLabel(
            "Materials with numbered textures (name.N or name_N) appear here. "
            "Select frames, add a state, then use viewport ▶ (animates 2+ frames, or sets a single frame)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #aaa; font-size: 11px;")
        root.addWidget(hint)

        mat_row = QHBoxLayout()
        mat_row.addWidget(QLabel("Material"))
        self._material_combo = QComboBox()
        self._material_combo.currentIndexChanged.connect(self._on_material_changed)
        mat_row.addWidget(self._material_combo, stretch=1)
        root.addLayout(mat_row)

        timing = QFormLayout()
        self._base_duration = QSpinBox()
        self._base_duration.setRange(16, 2000)
        self._base_duration.setSuffix(" ms")
        self._base_duration.setValue(DEFAULT_FRAME_DURATION_MS)
        self._base_duration.valueChanged.connect(self._on_timing_changed)
        timing.addRow("Frame duration", self._base_duration)
        root.addLayout(timing)

        tex_header = QHBoxLayout()
        tex_header.addWidget(QLabel("Detected frames"))
        tex_header.addStretch()
        self._select_all_btn = QPushButton("Select all")
        tex_header.addWidget(self._select_all_btn)
        self._clear_sel_btn = QPushButton("Clear")
        tex_header.addWidget(self._clear_sel_btn)
        root.addLayout(tex_header)

        self._frame_list = QListWidget()
        self._frame_list.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._frame_list.setMinimumHeight(88)
        self._select_all_btn.clicked.connect(self._frame_list.selectAll)
        self._clear_sel_btn.clicked.connect(self._frame_list.clearSelection)
        root.addWidget(self._frame_list)

        state_row = QHBoxLayout()
        self._state_name = QLineEdit()
        self._state_name.setPlaceholderText("State name (e.g. off, on, blink)")
        state_row.addWidget(self._state_name, stretch=1)
        self._add_state_btn = QPushButton("Add state")
        self._add_state_btn.setToolTip("Create a state from the selected frames")
        self._add_state_btn.clicked.connect(self._add_state_from_selection)
        state_row.addWidget(self._add_state_btn)
        self._play_all_btn = QPushButton("Use all frames")
        self._play_all_btn.setToolTip("Create a state using every detected frame")
        self._play_all_btn.clicked.connect(self._add_play_all_state)
        state_row.addWidget(self._play_all_btn)
        root.addLayout(state_row)

        root.addWidget(QLabel("States"))
        self._state_list = QListWidget()
        self._state_list.setMinimumHeight(88)
        root.addWidget(self._state_list)

        editor = QFormLayout()
        self._state_speed = QDoubleSpinBox()
        self._state_speed.setRange(0.05, 8.0)
        self._state_speed.setSingleStep(0.1)
        self._state_speed.setValue(1.0)
        self._state_speed.valueChanged.connect(self._on_state_editor_changed)
        editor.addRow("Speed", self._state_speed)
        self._state_loop = QCheckBox("Loop")
        self._state_loop.setChecked(True)
        self._state_loop.toggled.connect(self._on_state_editor_changed)
        editor.addRow("", self._state_loop)
        self._state_animate = QCheckBox("Animate (2+ frames)")
        self._state_animate.setChecked(True)
        self._state_animate.toggled.connect(self._on_state_editor_changed)
        editor.addRow("", self._state_animate)
        root.addLayout(editor)

        actions = QHBoxLayout()
        self._preview_btn = QPushButton("Preview state")
        self._preview_btn.clicked.connect(self._preview_selected_state)
        actions.addWidget(self._preview_btn)
        self._delete_btn = QPushButton("Delete state")
        self._delete_btn.clicked.connect(self._delete_selected_state)
        actions.addWidget(self._delete_btn)
        actions.addStretch()
        root.addLayout(actions)

        self._empty = QLabel("No numbered texture frames detected for this model.")
        self._empty.setWordWrap(True)
        self._empty.setStyleSheet("color: #888;")
        root.addWidget(self._empty)

        self._state_list.currentItemChanged.connect(self._on_state_selection_changed)

    def set_context(self, *, asset_id: str | None, materials: dict[str, dict]) -> None:
        self._asset_id = asset_id
        if not asset_id:
            self._materials = {}
        else:
            self._materials = {
                str(name): normalize_material_spec(dict(spec))
                for name, spec in materials.items()
                if isinstance(spec, dict)
            }
        self._rebuild_material_combo()

    def _rebuild_material_combo(self) -> None:
        self._building = True
        self._material_combo.blockSignals(True)
        self._material_combo.clear()
        has_materials = bool(self._materials)
        self._empty.setVisible(not has_materials)
        for widget in (
            self._material_combo,
            self._frame_list,
            self._state_list,
            self._state_name,
            self._add_state_btn,
            self._play_all_btn,
            self._select_all_btn,
            self._clear_sel_btn,
            self._base_duration,
            self._state_speed,
            self._state_loop,
            self._state_animate,
            self._preview_btn,
            self._delete_btn,
        ):
            widget.setEnabled(has_materials)
        if not has_materials:
            self._current_material = None
            self._frame_list.clear()
            self._state_list.clear()
            self._material_combo.blockSignals(False)
            self._building = False
            return
        for name in sorted(self._materials):
            self._material_combo.addItem(name, name)
        self._material_combo.blockSignals(False)
        self._building = False
        self._on_material_changed(self._material_combo.currentIndex())

    def _current_spec(self) -> dict | None:
        if not self._current_material:
            return None
        return self._materials.get(self._current_material)

    def _detected_frames(self) -> list[str]:
        spec = self._current_spec()
        if spec is None:
            return []
        return [str(frame) for frame in (spec.get("frames") or [])]

    def _emit_spec(self) -> None:
        if self._building or not self._current_material:
            return
        spec = self._current_spec()
        if spec is None:
            return
        self.spec_changed.emit(self._current_material, dict(spec))

    def _on_material_changed(self, _index: int) -> None:
        if self._building:
            return
        name = self._material_combo.currentData()
        self._current_material = str(name) if name else None
        self._refresh_frame_list()
        self._refresh_state_list()

    def _refresh_frame_list(self) -> None:
        self._building = True
        self._frame_list.clear()
        spec = self._current_spec()
        if spec is None:
            self._building = False
            return
        self._base_duration.blockSignals(True)
        self._base_duration.setValue(int(spec.get("frameDurationMs") or DEFAULT_FRAME_DURATION_MS))
        self._base_duration.blockSignals(False)
        for frame_key in self._detected_frames():
            item = QListWidgetItem(str(frame_key))
            if is_numbered_frame_key(frame_key):
                item.setToolTip("Numbered animation frame")
            self._frame_list.addItem(item)
        self._building = False

    def _refresh_state_list(self, *, select_name: str | None = None) -> None:
        self._building = True
        self._state_list.clear()
        spec = self._current_spec()
        if spec is None:
            self._building = False
            return
        selected_row = 0
        for row, name in enumerate(state_names(spec)):
            body = spec["states"][name]
            frames = body.get("frames") or []
            speed = float(body.get("speed") or 1.0)
            summary = f"{name}: {', '.join(frames)}  ({speed:.1f}×)"
            item = QListWidgetItem(summary)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self._state_list.addItem(item)
            if select_name and name == select_name:
                selected_row = row
        if self._state_list.count():
            self._state_list.setCurrentRow(selected_row)
        self._building = False
        self._load_state_editor()

    def _selected_state_name(self) -> str | None:
        item = self._state_list.currentItem()
        if item is None:
            return None
        name = item.data(Qt.ItemDataRole.UserRole)
        return str(name) if name else None

    def _load_state_editor(self) -> None:
        spec = self._current_spec()
        state_name = self._selected_state_name()
        if spec is None or not state_name:
            return
        body = spec.get("states", {}).get(state_name) or {}
        self._building = True
        self._state_speed.blockSignals(True)
        self._state_loop.blockSignals(True)
        self._state_animate.blockSignals(True)
        self._state_speed.setValue(float(body.get("speed") or 1.0))
        self._state_loop.setChecked(bool(body.get("loop", True)))
        frames = body.get("frames") or []
        self._state_animate.setChecked(bool(body.get("animate", len(frames) > 1)))
        self._state_speed.blockSignals(False)
        self._state_loop.blockSignals(False)
        self._state_animate.blockSignals(False)
        self._building = False

    def _on_state_selection_changed(self, _current, _previous) -> None:
        self._load_state_editor()

    def _on_timing_changed(self, value: int) -> None:
        spec = self._current_spec()
        if spec is None:
            return
        spec["frameDurationMs"] = int(value)
        self._emit_spec()

    def _on_state_editor_changed(self, *_args) -> None:
        spec = self._current_spec()
        state_name = self._selected_state_name()
        if spec is None or not state_name:
            return
        states = spec.setdefault("states", {})
        body = dict(states.get(state_name) or {})
        body["speed"] = float(self._state_speed.value())
        body["loop"] = bool(self._state_loop.isChecked())
        body["animate"] = bool(self._state_animate.isChecked())
        states[state_name] = body
        self._refresh_state_list(select_name=state_name)
        self._emit_spec()

    def _selected_frame_keys(self) -> list[str]:
        keys: list[str] = []
        for item in self._frame_list.selectedItems():
            text = str(item.text() or "").strip().casefold()
            if text:
                keys.append(text)
        return keys

    def _add_state(self, name: str, frames: list[str], *, animate: bool | None = None) -> None:
        spec = self._current_spec()
        if spec is None or not name or not frames:
            return
        states = spec.setdefault("states", {})
        states[name] = {
            "frames": list(frames),
            "animate": bool(animate if animate is not None else len(frames) > 1),
            "loop": True,
            "speed": 1.0,
        }
        spec["activeState"] = name
        self._refresh_state_list(select_name=name)
        self._emit_spec()

    def _add_state_from_selection(self) -> None:
        name = self._state_name.text().strip()
        frames = self._selected_frame_keys()
        if not name or not frames:
            return
        self._add_state(name, frames)
        self._state_name.clear()

    def _add_play_all_state(self) -> None:
        spec = self._current_spec()
        if spec is None:
            return
        frames = self._detected_frames()
        if not frames:
            return
        states = spec.setdefault("states", {})
        if DEFAULT_PLAY_STATE in states:
            states[DEFAULT_PLAY_STATE]["frames"] = frames
            states[DEFAULT_PLAY_STATE]["animate"] = len(frames) >= 2
            spec["activeState"] = DEFAULT_PLAY_STATE
            self._refresh_state_list(select_name=DEFAULT_PLAY_STATE)
            self._emit_spec()
            return
        self._add_state(DEFAULT_PLAY_STATE, frames, animate=len(frames) >= 2)

    def _preview_selected_state(self) -> None:
        state_name = self._selected_state_name()
        if not self._current_material or not state_name:
            return
        spec = self._current_spec()
        if spec is None:
            return
        spec["activeState"] = state_name
        self.preview_state_changed.emit(self._current_material, state_name)

    def _delete_selected_state(self) -> None:
        spec = self._current_spec()
        state_name = self._selected_state_name()
        if spec is None or not state_name:
            return
        states = spec.get("states") or {}
        states.pop(state_name, None)
        spec["states"] = states
        if spec.get("activeState") == state_name:
            spec["activeState"] = next(iter(states), "")
        self._refresh_state_list()
        self._emit_spec()
