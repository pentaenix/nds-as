"""3DS model inspector tabs: playable animation list + texture sheet browser
with eye-frame control. Populated from the built preview GLB, so it stays
inside the UI layer (no platform imports)."""
from __future__ import annotations

import json
import struct
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


def _parse_glb_summary(glb_path: Path) -> dict:
    """Extract animation names, image thumbnails and material->texture links
    from a GLB without external dependencies."""
    out = {"animations": [], "images": [], "materials": [], "forms": [], "default_form": ""}
    try:
        data = glb_path.read_bytes()
        if data[:4] != b"glTF":
            return out
        json_length = struct.unpack_from("<I", data, 12)[0]
        doc = json.loads(data[20 : 20 + json_length])
        bin_start = 20 + json_length + 8
        out["animations"] = [
            str(anim.get("name") or f"animation_{i}")
            for i, anim in enumerate(doc.get("animations", []))
        ]
        appearance = (doc.get("extras") or {}).get("rae", {}).get("appearanceVariants") or {}
        defaults = appearance.get("default") or {}
        out["default_form"] = str(defaults.get("form") or "")
        for axis in appearance.get("axes") or []:
            if axis.get("id") != "form":
                continue
            if not out["default_form"]:
                out["default_form"] = str(axis.get("default") or "")
            out["forms"] = [
                {
                    "id": str(option.get("id") or ""),
                    "label": str(option.get("label") or option.get("id") or ""),
                }
                for option in axis.get("options") or []
                if option.get("id")
            ]
            break
        views = doc.get("bufferViews", [])
        for image in doc.get("images", []):
            view = views[image["bufferView"]] if "bufferView" in image else None
            png = b""
            if view is not None:
                offset = bin_start + int(view.get("byteOffset", 0))
                png = data[offset : offset + int(view["byteLength"])]
            out["images"].append({"name": str(image.get("name") or ""), "png": png})
        textures = doc.get("textures", [])
        for material in doc.get("materials", []):
            base = material.get("pbrMetallicRoughness", {}).get("baseColorTexture", {})
            texture_name = ""
            if "index" in base:
                source = textures[base["index"]].get("source")
                if source is not None and source < len(out["images"]):
                    texture_name = out["images"][source]["name"]
            eye_sheet = (material.get("extras") or {}).get("rae", {}).get("eyeSheet")
            eye_expression = (material.get("extras") or {}).get("rae", {}).get("eyeExpression")
            entry = {"name": str(material.get("name") or ""), "texture": texture_name}
            if eye_sheet:
                entry["eyeSheet"] = eye_sheet
            if eye_expression:
                entry["eyeExpression"] = eye_expression
            out["materials"].append(entry)
    except Exception:
        pass
    return out


class ThreedsAnimationsWidget(QWidget):
    play_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)
        self._list = QListWidget()
        self._list.setToolTip("Double-click an animation to play it in the preview.")
        self._list.itemDoubleClicked.connect(self._on_double_click)
        layout.addWidget(self._list, stretch=1)
        buttons = QHBoxLayout()
        self._play = QPushButton("Play")
        self._play.clicked.connect(self._on_play)
        self._stop = QPushButton("Stop")
        self._stop.clicked.connect(self.stop_requested.emit)
        buttons.addWidget(self._play)
        buttons.addWidget(self._stop)
        buttons.addStretch()
        layout.addLayout(buttons)

    def set_animations(self, names: list[str]) -> None:
        self._list.clear()
        for name in names:
            self._list.addItem(QListWidgetItem(name))

    def _selected_name(self) -> str:
        item = self._list.currentItem()
        return item.text() if item is not None else ""

    def _on_play(self) -> None:
        name = self._selected_name()
        if not name and self._list.count():
            self._list.setCurrentRow(0)
            name = self._selected_name()
        if name:
            self.play_requested.emit(name)

    def _on_double_click(self, item: QListWidgetItem) -> None:
        self.play_requested.emit(item.text())


class ThreedsTexturesWidget(QWidget):
    eye_frame_changed = Signal(str, int)  # material, frame index (0-based)
    shiny_toggled = Signal(bool)
    form_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        outer.addWidget(scroll)
        self._content = QWidget()
        scroll.setWidget(self._content)
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(8)
        self._eye_expressions: dict[str, dict] = {}
        self._shiny_toggle = QCheckBox("Shiny textures")
        self._shiny_toggle.setToolTip("Preview the shiny texture set for this Pokémon model.")
        self._shiny_toggle.toggled.connect(self.shiny_toggled.emit)
        self._shiny_toggle.hide()
        self._form_combo = QComboBox()
        self._form_combo.setToolTip("Preview an embedded Pokémon form/pattern variant.")
        self._form_combo.currentIndexChanged.connect(self._emit_form_changed)
        self._form_combo.hide()

    def set_shiny_available(self, available: bool, *, checked: bool = False) -> None:
        self._shiny_toggle.setVisible(available)
        self._shiny_toggle.blockSignals(True)
        self._shiny_toggle.setChecked(checked)
        self._shiny_toggle.blockSignals(False)

    def sync_shiny_checked(self, checked: bool) -> None:
        if not self._shiny_toggle.isVisible():
            return
        self._shiny_toggle.blockSignals(True)
        self._shiny_toggle.setChecked(checked)
        self._shiny_toggle.blockSignals(False)

    def set_forms(self, forms: list[dict], default_form: str = "") -> None:
        self._form_combo.blockSignals(True)
        self._form_combo.clear()
        for form in forms:
            self._form_combo.addItem(form.get("label") or form.get("id") or "", form.get("id") or "")
        index = 0
        if default_form:
            for i in range(self._form_combo.count()):
                if self._form_combo.itemData(i) == default_form:
                    index = i
                    break
        if self._form_combo.count():
            self._form_combo.setCurrentIndex(index)
        self._form_combo.setVisible(self._form_combo.count() > 1)
        self._form_combo.blockSignals(False)

    def set_context(self, images: list[dict], materials: list[dict]) -> None:
        if self._shiny_toggle.parent() is self._content:
            self._layout.removeWidget(self._shiny_toggle)
        if self._form_combo.parent() is self._content:
            self._layout.removeWidget(self._form_combo)
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None and widget not in (self._shiny_toggle, self._form_combo):
                widget.deleteLater()

        self._layout.addWidget(self._form_combo)
        self._layout.addWidget(self._shiny_toggle)

        eye_materials = [
            mat
            for mat in materials
            if mat.get("eyeExpression")
        ]
        self._eye_expressions = {
            mat["name"]: mat["eyeExpression"]
            for mat in eye_materials
        }
        if eye_materials:
            eye_box = QWidget()
            eye_layout = QGridLayout(eye_box)
            eye_layout.setContentsMargins(0, 0, 0, 0)
            title = QLabel("Eye frames")
            title.setStyleSheet("font-weight: bold;")
            eye_layout.addWidget(title, 0, 0, 1, 2)
            for row, mat in enumerate(eye_materials, start=1):
                expr = mat["eyeExpression"]
                frame_count = int(expr.get("frameCount") or 0)
                eye_layout.addWidget(QLabel(mat["name"]), row, 0)
                combo = QComboBox()
                combo.blockSignals(True)
                for frame in range(frame_count):
                    combo.addItem(f"Frame {frame}", frame)
                combo.setCurrentIndex(0)
                combo.blockSignals(False)
                combo.setToolTip(
                    "Eye expression frame (0-based). Sclera only; iris unchanged."
                )
                combo.currentIndexChanged.connect(
                    lambda index, name=mat["name"], c=combo: self._emit_eye_frame(name, c)
                )
                eye_layout.addWidget(combo, row, 1)
            self._layout.addWidget(eye_box)

        if images:
            grid_box = QWidget()
            grid = QGridLayout(grid_box)
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setSpacing(6)
            columns = 3
            for i, image in enumerate(images):
                cell = QWidget()
                cell_layout = QVBoxLayout(cell)
                cell_layout.setContentsMargins(0, 0, 0, 0)
                cell_layout.setSpacing(2)
                thumb = QLabel()
                thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
                qimage = QImage.fromData(image.get("png") or b"")
                if not qimage.isNull():
                    thumb.setPixmap(
                        QPixmap.fromImage(qimage).scaled(
                            110,
                            110,
                            Qt.AspectRatioMode.KeepAspectRatio,
                            Qt.TransformationMode.FastTransformation,
                        )
                    )
                caption = QLabel(image.get("name") or "")
                caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
                caption.setWordWrap(True)
                caption.setStyleSheet("font-size: 10px;")
                cell_layout.addWidget(thumb)
                cell_layout.addWidget(caption)
                grid.addWidget(cell, i // columns, i % columns)
            self._layout.addWidget(grid_box)
        self._layout.addStretch()

    def _emit_eye_frame(self, material_name: str, combo: QComboBox) -> None:
        frame = int(combo.currentData() if combo.currentData() is not None else 0)
        if material_name not in self._eye_expressions:
            return
        self.eye_frame_changed.emit(material_name, frame)

    def _emit_form_changed(self) -> None:
        form_id = self._form_combo.currentData()
        if form_id:
            self.form_changed.emit(str(form_id))


class ThreedsPanelMixin:
    """Adds Animations/Textures inspector tabs for 3DS model previews."""

    def setup_threeds_inspector_tabs(self) -> None:
        tabs = getattr(self, "preview_inspector_tabs", None)
        if tabs is None:
            return
        self.threeds_animations = ThreedsAnimationsWidget()
        self.threeds_animations.play_requested.connect(self._on_threeds_play_animation)
        self.threeds_animations.stop_requested.connect(self._on_threeds_stop_animation)
        self.threeds_textures = ThreedsTexturesWidget()
        self.threeds_textures.eye_frame_changed.connect(self._on_threeds_eye_frame)
        self.threeds_textures.shiny_toggled.connect(self._on_threeds_shiny_toggled)
        self.threeds_textures.form_changed.connect(self._on_threeds_form_changed)
        self._inspector_tab_threeds_animations = tabs.addTab(self.threeds_animations, "Animations")
        self._inspector_tab_threeds_textures = tabs.addTab(self.threeds_textures, "Textures")
        tabs.setTabVisible(self._inspector_tab_threeds_animations, False)
        tabs.setTabVisible(self._inspector_tab_threeds_textures, False)
        self._threeds_tabs_asset_id: str | None = None
        self._threeds_has_animations = False

    def show_threeds_model_tabs(self, glb_path: Path, asset_id: str) -> None:
        tabs = getattr(self, "preview_inspector_tabs", None)
        if tabs is None or not hasattr(self, "threeds_animations"):
            return
        summary = _parse_glb_summary(Path(glb_path))
        self.threeds_animations.set_animations(summary["animations"])
        self.threeds_textures.set_forms(summary.get("forms") or [], summary.get("default_form") or "")
        self.threeds_textures.set_context(summary["images"], summary["materials"])
        shiny = bool(getattr(self, "_threeds_preview_shiny", False))
        self.threeds_textures.set_shiny_available(True, checked=shiny)
        self._threeds_tabs_asset_id = asset_id
        self._threeds_has_animations = bool(summary["animations"])
        self._last_previewed_asset_id = asset_id
        self._sync_threeds_tabs()

    def _sync_threeds_tabs(self, asset=None) -> None:
        tabs = getattr(self, "preview_inspector_tabs", None)
        if tabs is None or not hasattr(self, "_inspector_tab_threeds_animations"):
            return
        asset = asset or (self.selected_asset() if hasattr(self, "selected_asset") else None)
        active = bool(
            asset is not None
            and self._threeds_tabs_asset_id
            and asset.asset_id == self._threeds_tabs_asset_id
            and getattr(self, "_last_previewed_asset_id", None) == asset.asset_id
        )
        tabs.setTabVisible(
            self._inspector_tab_threeds_animations, active and self._threeds_has_animations
        )
        tabs.setTabVisible(self._inspector_tab_threeds_textures, active)
        if hasattr(self, "threeds_textures"):
            self.threeds_textures.set_shiny_available(
                active,
                checked=bool(getattr(self, "_threeds_preview_shiny", False)),
            )
        if not tabs.isTabVisible(tabs.currentIndex()):
            tabs.setCurrentIndex(getattr(self, "_inspector_tab_preview", 0))

    def _sync_preview_inspector_tabs(self, asset=None) -> None:  # type: ignore[override]
        super()._sync_preview_inspector_tabs(asset)  # type: ignore[misc]
        self._sync_threeds_tabs(asset)

    def _on_threeds_play_animation(self, name: str) -> None:
        preview = getattr(self, "preview", None)
        if preview is not None and hasattr(preview, "play_glb_animation"):
            preview.play_glb_animation(name)

    def _on_threeds_stop_animation(self) -> None:
        preview = getattr(self, "preview", None)
        if preview is not None and hasattr(preview, "stop_glb_animation"):
            preview.stop_glb_animation()

    def sync_threeds_shiny_toggle(self, checked: bool) -> None:
        if hasattr(self, "threeds_textures"):
            self.threeds_textures.sync_shiny_checked(checked)

    def _on_threeds_shiny_toggled(self, checked: bool) -> None:
        if getattr(self, "_threeds_preview_shiny", False) == checked:
            return
        from ...core.modules import get_platform_modules

        model_module = get_platform_modules("3ds").model
        if hasattr(model_module, "set_preview_shiny"):
            model_module.set_preview_shiny(self, shiny=checked)

    def _on_threeds_eye_frame(self, material_name: str, frame_index: int) -> None:
        preview = getattr(self, "preview", None)
        if preview is not None and hasattr(preview, "set_eye_expression_frame"):
            preview.set_eye_expression_frame(material_name, frame_index)

    def _on_threeds_form_changed(self, form_id: str) -> None:
        preview = getattr(self, "preview", None)
        if preview is not None and hasattr(preview, "set_form_variant"):
            preview.set_form_variant(form_id)
