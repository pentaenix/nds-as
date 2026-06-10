"""Preview widget shell: layout, image display, and control chrome."""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, QPoint
from PySide6.QtGui import QAction, QBrush, QColor, QPainter, QPixmap, QWheelEvent
from PySide6.QtWidgets import QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget

from ..constants import CHROME_BUTTON_STYLE, CHECKER_DARK, CHECKER_LIGHT, GL_BG_COLORS, VIEWPORT_BANNER_STYLE
from .canvas import PreviewCanvas, PreviewGLView
from .colors import qcolor_rgbf
from .glb_preview import GlbPreviewMixin
from .image_label import PreviewImageLabel
from .web_preview import WebGlbPreviewWidget, webengine_preview_available


class PreviewWidget(GlbPreviewMixin, QWidget):
    """OpenGL / image preview canvas with a compact top chrome row."""

    def __init__(self):
        super().__init__()
        self._available = False
        self._view = None
        self._web_view: WebGlbPreviewWidget | None = None
        self._use_legacy_gl = os.environ.get("RAE_LEGACY_GL_PREVIEW", "").strip() in {"1", "true", "yes"}
        self._mesh_items = []
        self._image_label = None
        self._last_path: Path | None = None
        self._fallback_texture_paths: list[Path] = []
        self._texture_by_name: dict[str, Path] = {}
        self._material_to_texture: dict[str, str] = {}
        self._texture_bind_order: list[str] = []
        self._fallback_texture_image = None
        self._fallback_texture_images: dict[str, object] = {}
        self._fallback_texture_path_order: list[Path] = []
        self._use_textures = True
        self._wireframe = False
        self._background_name = "Checkered"
        self._image_source: QPixmap | None = None
        self._image_zoom = 1.0
        self._model_distance = 90.0
        self._flipbook_clips: list[dict] = []
        self._flipbook_playback_options: list[dict] = []
        self._flipbook_autoplay_pending = False

        self.textures_toggle = QPushButton()
        self.textures_toggle.setCheckable(True)
        self.textures_toggle.setChecked(True)
        self.textures_toggle.hide()
        self.wireframe_toggle = QPushButton()
        self.wireframe_toggle.setCheckable(True)
        self.wireframe_toggle.hide()
        self.textures_toggle.toggled.connect(self.set_use_textures)
        self.wireframe_toggle.toggled.connect(self.set_wireframe)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        chrome = QWidget()
        chrome_layout = QHBoxLayout(chrome)
        chrome_layout.setContentsMargins(6, 4, 6, 4)
        chrome_layout.setSpacing(6)
        chrome_layout.addStretch()

        self._action_host = QWidget()
        self.action_layout = QHBoxLayout(self._action_host)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(6)
        chrome_layout.addWidget(self._action_host)

        self._bg_combo = QComboBox()
        self._bg_combo.addItems(["Checkered", "White", "Black"])
        self._bg_combo.setCurrentIndex(0)
        self._bg_combo.setToolTip("Preview background")
        self._bg_combo.setFixedWidth(96)
        self._bg_combo.currentTextChanged.connect(self._on_background_changed)
        chrome_layout.addWidget(self._bg_combo)

        self._checkered_brush = self._make_checkered_brush()
        self._canvas = PreviewCanvas(self)
        self._canvas.setMinimumHeight(280)
        self._canvas.setMouseTracking(True)
        canvas_layout = QGridLayout(self._canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(VIEWPORT_BANNER_STYLE)
        self._banner.hide()
        self._banner.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        if webengine_preview_available() and not self._use_legacy_gl:
            self._web_view = WebGlbPreviewWidget(self._canvas)
            canvas_layout.addWidget(self._web_view, 0, 0)
            self._web_view.set_background_name(self._background_name)
            self._available = True
        else:
            try:
                import numpy as np  # noqa: F401
                import trimesh  # noqa: F401
                import pyqtgraph.opengl as gl  # noqa: F401

                view_cls = PreviewGLView if PreviewGLView is not None else gl.GLViewWidget
                self._view = view_cls()
                self._view.setCameraPosition(distance=self._model_distance)
                if hasattr(self._view, "set_preview_background_name"):
                    self._view.set_preview_background_name(self._background_name)
                else:
                    self._view.opts["bgcolor"] = (0.28, 0.28, 0.28, 1.0)
                self._axis = gl.GLAxisItem()
                self._view.addItem(self._axis)
                canvas_layout.addWidget(self._view, 0, 0)
                self._available = True
            except Exception:
                placeholder = QLabel(
                    "3D preview unavailable.\nInstall PySide6-WebEngine (or requirements.txt) to enable model preview."
                )
                placeholder.setAlignment(Qt.AlignCenter)
                placeholder.setWordWrap(True)
                placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
                canvas_layout.addWidget(placeholder, 0, 0)

        self._image_label = PreviewImageLabel()
        canvas_layout.addWidget(self._image_label, 0, 0)
        canvas_layout.setRowStretch(0, 1)
        canvas_layout.setColumnStretch(0, 1)
        self._image_label.hide()
        self._image_label.installEventFilter(self)

        self._message_label = QLabel()
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.setStyleSheet("background: transparent; color: #b8b8b8; padding: 16px;")
        self._message_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        canvas_layout.addWidget(self._message_label, 0, 0)
        self._message_label.hide()

        canvas_layout.addWidget(self._banner, 0, 0, alignment=Qt.AlignTop)

        self._flipbook_controls = QWidget(self._canvas)
        flip_outer = QVBoxLayout(self._flipbook_controls)
        flip_outer.setContentsMargins(0, 0, 0, 0)
        flip_outer.setSpacing(4)
        flip_row = QHBoxLayout()
        flip_row.setContentsMargins(0, 0, 0, 0)
        self._flipbook_play_btn = QPushButton("▶")
        self._flipbook_play_btn.setCheckable(True)
        self._flipbook_play_btn.setFixedSize(34, 28)
        self._flipbook_play_btn.setToolTip("Play / pause texture animation preview")
        self._flipbook_play_btn.setStyleSheet(CHROME_BUTTON_STYLE)
        self._flipbook_play_btn.toggled.connect(self._on_flipbook_play_toggled)
        flip_row.addWidget(self._flipbook_play_btn)
        flip_outer.addLayout(flip_row)
        self._flipbook_state_combo = QComboBox()
        self._flipbook_state_combo.setToolTip("Choose which animation state to play")
        self._flipbook_state_combo.setMinimumWidth(140)
        self._flipbook_state_combo.currentIndexChanged.connect(self._on_flipbook_state_changed)
        self._flipbook_state_combo.hide()
        flip_outer.addWidget(self._flipbook_state_combo)
        self._flipbook_controls.hide()

        root.addWidget(chrome)
        root.addWidget(self._canvas, stretch=1)
        self._apply_background("Checkered")
        self.setFocusPolicy(Qt.StrongFocus)
        self._canvas.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.installEventFilter(self)
        self._canvas.installEventFilter(self)
        if self._view is not None:
            self._view.installEventFilter(self)

    def _make_checkered_brush(self) -> QBrush:
        tile = 14
        pm = QPixmap(tile * 2, tile * 2)
        pm.fill(QColor(CHECKER_LIGHT))
        painter = QPainter(pm)
        painter.fillRect(0, 0, tile, tile, QColor(CHECKER_DARK))
        painter.fillRect(tile, tile, tile, tile, QColor(CHECKER_DARK))
        painter.end()
        return QBrush(pm)

    def set_banner(self, text: str = "") -> None:
        text = str(text or "").strip()
        if text:
            self._banner.setText(text)
            self._banner.show()
            self._banner.raise_()
        else:
            self._banner.clear()
            self._banner.hide()

    def _on_background_changed(self, name: str) -> None:
        self._apply_background(name)

    def _refresh_background_tiles(self) -> None:
        self._canvas.update()
        if self._web_view is not None:
            self._web_view.set_background_name(self._background_name)
        elif self._view is not None:
            if hasattr(self._view, "set_preview_background_name"):
                self._view.set_preview_background_name(self._background_name)
            else:
                rgba = GL_BG_COLORS.get(self._background_name, GL_BG_COLORS["Checkered"])
                try:
                    self._view.opts["bgcolor"] = rgba
                except Exception:
                    pass
            try:
                self._view.update()
            except Exception:
                pass

    def _apply_background(self, name: str) -> None:
        allowed = {"White", "Checkered", "Black"}
        self._background_name = name if name in allowed else "Checkered"
        self._refresh_background_tiles()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_background_tiles()
        if self._image_source is not None and not self._image_source.isNull():
            self._refresh_image_display()
        self._position_flipbook_controls()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        if self._image_source is not None and not self._image_source.isNull() and self._image_label.isVisible():
            factor = 1.12 if delta > 0 else 1 / 1.12
            self._image_zoom = max(0.08, min(12.0, self._image_zoom * factor))
            self._refresh_image_display()
            event.accept()
            return
        if self._web_view is not None and self._web_view.isVisible():
            event.ignore()
            return
        if self._view is not None and self._view.isVisible() and self._mesh_items:
            factor = 0.9 if delta > 0 else 1.1
            self._model_distance = max(8.0, min(600.0, self._model_distance * factor))
            try:
                self._view.setCameraPosition(distance=self._model_distance)
            except Exception:
                pass
            event.accept()
            return
        super().wheelEvent(event)

    def _refresh_image_display(self) -> None:
        if self._image_source is None or self._image_source.isNull():
            return
        src_w = max(1, self._image_source.width())
        src_h = max(1, self._image_source.height())
        canvas_w = max(1, self._canvas.width() - 24)
        canvas_h = max(1, self._canvas.height() - 24)
        fit = min(canvas_w / src_w, canvas_h / src_h, 1.0)
        scale = fit * self._image_zoom
        if scale >= 1.0:
            pixel_scale = max(1, int(round(scale)))
            target_w = src_w * pixel_scale
            target_h = src_h * pixel_scale
        else:
            target_w = max(1, int(src_w * scale))
            target_h = max(1, int(src_h * scale))
        scaled = self._image_source.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        scaled.setDevicePixelRatio(1.0)
        self._image_label.set_preview_pixmap(scaled)

    def _image_pan_global_pos(self, event) -> QPoint:
        if hasattr(event, "globalPosition"):
            return event.globalPosition().toPoint()
        return event.globalPos()

    def _handle_image_pan_event(self, watched, event) -> bool:
        if not self._image_label.isVisible() or self._image_source is None:
            return False
        et = event.type()
        if et == QEvent.Type.MouseButtonPress and event.button() == Qt.LeftButton:
            return self._image_label.start_pan_at_global(self._image_pan_global_pos(event))
        if et == QEvent.Type.MouseMove and self._image_label.is_panning():
            self._image_label.move_pan_at_global(self._image_pan_global_pos(event))
            return True
        if et == QEvent.Type.MouseButtonRelease and event.button() == Qt.LeftButton and self._image_label.is_panning():
            self._image_label.end_pan()
            return True
        return False

    def eventFilter(self, watched, event) -> bool:
        if self._image_label.isVisible() and watched in {self._canvas, self._image_label}:
            et = event.type()
            if et in {
                QEvent.Type.MouseButtonPress,
                QEvent.Type.MouseMove,
                QEvent.Type.MouseButtonRelease,
            }:
                if self._handle_image_pan_event(watched, event):
                    event.accept()
                    return True
        if event.type() == QEvent.Type.Wheel and watched in {self, self._canvas, self._view, self._image_label}:
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)

    def _clear_meshes(self) -> None:
        if self._web_view is not None:
            self._web_view.clear_scene()
            return
        if self._view is not None:
            for item in self._mesh_items:
                dispose = getattr(item, "dispose_gl", None)
                if callable(dispose):
                    try:
                        dispose()
                    except Exception:
                        pass
            self._view.clear()
            self._mesh_items.clear()
            self._view.update()

    def clear(self) -> None:
        self._clear_meshes()
        self._image_source = None
        self._image_zoom = 1.0
        self._image_label.reset_pan()
        self._image_label.clear()
        self._image_label.hide()
        self._message_label.clear()
        self._message_label.hide()
        if self._web_view is not None:
            self._web_view.hide()
        if self._view is not None:
            self._view.hide()

    def set_use_textures(self, enabled: bool) -> None:
        self._use_textures = enabled
        if self._last_path is not None:
            self.load_glb(
                self._last_path,
                fallback_textures=self._fallback_texture_paths,
                texture_by_name=self._texture_by_name,
                material_to_texture=self._material_to_texture,
                texture_bind_order=self._texture_bind_order,
                mesh_texture_overrides=getattr(self, "_mesh_texture_overrides", {}),
            )

    def reset_view(self) -> None:
        if self._web_view is not None and self._web_view.is_available():
            self._web_view.reset_view()
            return
        if self._view is not None and self._last_path is not None:
            self._model_distance = 90.0
            self._view.setCameraPosition(distance=90.0, elevation=30, azimuth=45)
            self._view.update()

    def _update_flipbook_controls(
        self,
        clips: list[dict],
        playback_options: list[dict] | None = None,
        *,
        autoplay: bool = False,
    ) -> None:
        self._flipbook_clips = list(clips)
        self._flipbook_playback_options = list(playback_options or [])
        self._flipbook_play_btn.blockSignals(True)
        self._flipbook_play_btn.setChecked(False)
        self._flipbook_play_btn.setText("▶")
        self._flipbook_play_btn.blockSignals(False)

        self._flipbook_state_combo.blockSignals(True)
        self._flipbook_state_combo.clear()
        if len(self._flipbook_playback_options) > 1:
            for option in self._flipbook_playback_options:
                self._flipbook_state_combo.addItem(option.get("label") or option.get("state") or "", option)
            self._flipbook_state_combo.show()
        else:
            self._flipbook_state_combo.hide()
        self._flipbook_state_combo.blockSignals(False)

        if clips:
            self._flipbook_controls.show()
            self._position_flipbook_controls()
            if autoplay and clips:
                self._flipbook_play_btn.blockSignals(True)
                self._flipbook_play_btn.setChecked(True)
                self._flipbook_play_btn.setText("⏸")
                self._flipbook_play_btn.blockSignals(False)
                self._start_selected_flipbook()
        else:
            self._flipbook_controls.hide()
            if self._web_view is not None and self._web_view.is_available():
                self._web_view.pause_flipbook()

    def _selected_flipbook_playback(self) -> list[dict]:
        if not self._flipbook_clips:
            return []
        if len(self._flipbook_playback_options) <= 1:
            return self._flipbook_clips
        option = self._flipbook_state_combo.currentData()
        if not isinstance(option, dict):
            return self._flipbook_clips
        material = str(option.get("material") or "")
        state = str(option.get("state") or "")
        matched = [
            clip
            for clip in self._flipbook_clips
            if clip.get("material") == material and clip.get("state") == state
        ]
        return matched or self._flipbook_clips

    @staticmethod
    def _flipbook_clip_is_static(clip: dict) -> bool:
        if clip.get("animate") is False:
            return True
        return len(clip.get("frameUrls") or []) < 2

    def _selected_flipbook_is_static(self) -> bool:
        clips = self._selected_flipbook_playback()
        return bool(clips) and all(self._flipbook_clip_is_static(clip) for clip in clips)

    def _reset_flipbook_play_button(self) -> None:
        self._flipbook_play_btn.blockSignals(True)
        self._flipbook_play_btn.setChecked(False)
        self._flipbook_play_btn.setText("▶")
        self._flipbook_play_btn.blockSignals(False)

    def _start_selected_flipbook(self) -> None:
        if self._web_view is None or not self._web_view.is_available():
            return
        from .texture_clips import clips_to_json

        clips = self._selected_flipbook_playback()
        if not clips:
            return
        self._web_view.start_flipbook(clips_to_json(clips))
        if self._selected_flipbook_is_static():
            self._reset_flipbook_play_button()

    def _on_flipbook_state_changed(self, _index: int) -> None:
        clips = self._selected_flipbook_playback()
        if not clips:
            return
        if self._selected_flipbook_is_static() or self._flipbook_play_btn.isChecked():
            self._start_selected_flipbook()

    def _on_flipbook_play_toggled(self, playing: bool) -> None:
        if self._web_view is None or not self._web_view.is_available():
            return
        if playing:
            if not self._selected_flipbook_playback():
                self._reset_flipbook_play_button()
                return
            if self._selected_flipbook_is_static():
                self._start_selected_flipbook()
                return
            self._flipbook_play_btn.setText("⏸")
            self._start_selected_flipbook()
        else:
            self._flipbook_play_btn.setText("▶")
            self._web_view.pause_flipbook()

    def _position_flipbook_controls(self) -> None:
        margin = 8
        self._flipbook_controls.adjustSize()
        width = self._flipbook_controls.width()
        self._flipbook_controls.move(max(0, self._canvas.width() - width - margin), margin)
        self._flipbook_controls.raise_()

    def set_wireframe(self, enabled: bool) -> None:
        self._wireframe = enabled
        if self._web_view is not None:
            self._web_view.set_wireframe(enabled)
            return
        if self._last_path is not None:
            self.load_glb(
                self._last_path,
                fallback_textures=self._fallback_texture_paths,
                texture_by_name=self._texture_by_name,
                material_to_texture=self._material_to_texture,
                texture_bind_order=self._texture_bind_order,
                mesh_texture_overrides=getattr(self, "_mesh_texture_overrides", {}),
            )

    def show_message(self, text: str) -> None:
        self.clear()
        self.set_banner("")
        text = str(text or "").strip()
        if text:
            self._message_label.setText(text)
            self._message_label.show()
        self._refresh_background_tiles()

    def show_image_path(self, path: Path, caption: str = "") -> None:
        self.clear()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.set_banner(f"Could not load image preview: {path.name}")
            return
        self.set_banner(caption if caption and "warning" in caption.lower() else "")
        self._message_label.hide()
        self._image_source = pixmap
        self._image_zoom = 1.0
        self._image_label.reset_pan()
        if self._web_view is not None:
            self._web_view.hide()
        if self._view is not None:
            self._view.hide()
        self._refresh_image_display()
        self._image_label.show()
        self._refresh_background_tiles()
