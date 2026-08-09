"""QGraphicsItems for EasyFind canvas groups and nodes."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QGraphicsItem

from ...easyfind.models import EasyFindNode
from .canvas_lod import LOD_HIGH, LOD_LABELS_ONLY, LOD_LOW, preview_pixel_size

TILE_FILL = QColor(58, 58, 58)
TILE_BORDER = QColor(80, 80, 80)
TILE_HOVER = QColor(110, 110, 110)
TILE_SELECTED = QColor(90, 140, 220)


class EasyFindGroupRegionItem(QGraphicsItem):
    """Colored outline rectangle with a label on top."""

    def __init__(
        self,
        group_id: str,
        title: str,
        level: int,
        accent_rgb: tuple[int, int, int],
        width: float,
        height: float,
    ) -> None:
        super().__init__()
        self.group_id = group_id
        self.title = title
        self.level = level
        self._accent = QColor(*accent_rgb)
        self._width = width
        self._height = height
        self.setZValue(float(level) * 0.1)
        self.setAcceptedMouseButtons(Qt.MouseButton.NoButton)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._width, self._height)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        rect = self.boundingRect()
        border_w = max(2.0, 3.0 - self.level * 0.5)
        fill_alpha = max(12, 28 - self.level * 6)
        fill = QColor(self._accent)
        fill.setAlpha(fill_alpha)
        painter.setPen(QPen(self._accent, border_w))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, 10, 10)

        label_h = 22.0
        label_rect = QRectF(12, 6, min(rect.width() - 24, 280), label_h)
        label_bg = QColor(self._accent)
        label_bg.setAlpha(210)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(label_bg))
        painter.drawRoundedRect(label_rect, 4, 4)

        title_font = QFont()
        title_font.setPointSize(10 if self.level else 11)
        title_font.setBold(True)
        painter.setFont(title_font)
        text_color = QColor(20, 20, 20) if self._accent.lightness() > 160 else QColor(250, 250, 250)
        painter.setPen(text_color)
        painter.drawText(label_rect.adjusted(8, 0, -8, 0), Qt.AlignmentFlag.AlignVCenter, self.title)


class EasyFindSectionItem(EasyFindGroupRegionItem):
    """Backward-compatible alias."""
    pass


class EasyFindClusterPortalItem(QGraphicsItem):
    """Coarse map tile; click the arrow to open the cluster detail layout."""

    _EXPAND_BTN = 30.0

    def __init__(
        self,
        cluster_id: str,
        title: str,
        accent_rgb: tuple[int, int, int],
        width: float,
        height: float,
        node_count: int,
    ) -> None:
        super().__init__()
        self.cluster_id = cluster_id
        self.title = title
        self._accent = QColor(*accent_rgb)
        self._width = width
        self._height = height
        self._node_count = node_count
        self._hovered = False
        self._expand_hovered = False
        self._loading = False
        self._spin_angle = 0
        self.setZValue(1.0)
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def is_loading(self) -> bool:
        return self._loading

    def set_loading(self, loading: bool) -> None:
        self._loading = bool(loading)
        if not self._loading:
            self._spin_angle = 0
        self.update()

    def advance_spinner(self) -> None:
        if not self._loading:
            return
        self._spin_angle = (self._spin_angle + 36) % 360
        self.update()

    def expand_button_rect(self) -> QRectF:
        margin = 10.0
        size = self._EXPAND_BTN
        return QRectF(
            self._width - margin - size,
            self._height - margin - size,
            size,
            size,
        )

    def hit_expand_button(self, local_pos) -> bool:
        return self.expand_button_rect().contains(local_pos)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._width, self._height)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self._expand_hovered = self.hit_expand_button(event.pos())
        self.update()
        super().hoverEnterEvent(event)

    def hoverMoveEvent(self, event) -> None:  # noqa: N802
        expand_hovered = self.hit_expand_button(event.pos())
        if expand_hovered != self._expand_hovered:
            self._expand_hovered = expand_hovered
            self.update()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self._expand_hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        rect = self.boundingRect()
        fill = QColor(self._accent)
        fill.setAlpha(42 if not self._hovered else 64)
        border = QColor(self._accent)
        border.setAlpha(220 if not self._hovered else 255)
        painter.setPen(QPen(border, 2.5 if self._hovered else 2.0))
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(rect, 12, 12)

        title_font = QFont()
        title_font.setPointSize(12)
        title_font.setBold(True)
        painter.setFont(title_font)
        text_color = QColor(20, 20, 20) if self._accent.lightness() > 160 else QColor(250, 250, 250)
        painter.setPen(text_color)
        painter.drawText(
            rect.adjusted(14, 14, -14, -48),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap,
            self.title,
        )

        count_font = QFont()
        count_font.setPointSize(10)
        painter.setFont(count_font)
        painter.setPen(QColor(210, 210, 210))
        count_text = f"{self._node_count:,} assets"
        painter.drawText(
            rect.adjusted(14, 0, -52, -14),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            count_text,
        )

        self._paint_expand_button(painter)

    def _paint_expand_button(self, painter: QPainter) -> None:
        btn = self.expand_button_rect()
        bg = QColor(32, 32, 32, 220)
        if self._expand_hovered and not self._loading:
            bg = QColor(52, 52, 52, 235)
        painter.setPen(QPen(QColor(190, 190, 190), 1.5))
        painter.setBrush(QBrush(bg))
        painter.drawRoundedRect(btn, 6, 6)

        if self._loading:
            painter.setPen(QPen(QColor(220, 220, 220), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            inset = btn.adjusted(7, 7, -7, -7)
            painter.drawArc(
                inset.toRect(),
                int(self._spin_angle * 16),
                int(270 * 16),
            )
            return

        cx = btn.center().x()
        cy = btn.center().y()
        arrow_w = 7.0
        arrow_h = 9.0
        arrow = QPolygonF([
            QPointF(cx - arrow_w * 0.35, cy - arrow_h * 0.5),
            QPointF(cx + arrow_w * 0.65, cy),
            QPointF(cx - arrow_w * 0.35, cy + arrow_h * 0.5),
        ])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(235, 235, 235)))
        painter.drawPolygon(arrow)


class EasyFindNodeItem(QGraphicsItem):
    def __init__(self, node: EasyFindNode) -> None:
        super().__init__()
        self.node = node
        self._width = 96.0
        self._height = 96.0
        self._hovered = False
        self._selected = False
        self._source_pixmap: QPixmap | None = None
        self._display_pixmap: QPixmap | None = None
        self._display_lod = LOD_LABELS_ONLY
        self._cached_lod = -1
        self.setAcceptHoverEvents(True)
        self.setZValue(2.0)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)

    def boundingRect(self) -> QRectF:  # noqa: N802
        return QRectF(0, 0, self._width, self._height)

    def set_selected_state(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def clear_preview(self) -> None:
        self._source_pixmap = None
        self._display_pixmap = None
        self._cached_lod = -1
        self.setCacheMode(QGraphicsItem.CacheMode.NoCache)
        self.update()

    def set_preview_source(self, pixmap: QPixmap) -> None:
        self._source_pixmap = pixmap if pixmap is not None and not pixmap.isNull() else None
        self._cached_lod = -1
        self._refresh_display_pixmap()

    def set_display_lod(self, lod: int) -> None:
        if lod == self._display_lod and self._cached_lod == lod:
            return
        self._display_lod = lod
        self._refresh_display_pixmap()
        if lod <= LOD_LOW:
            self.setCacheMode(QGraphicsItem.CacheMode.DeviceCoordinateCache)
        else:
            self.setCacheMode(QGraphicsItem.CacheMode.NoCache)

    def _refresh_display_pixmap(self) -> None:
        lod = self._display_lod
        if lod <= LOD_LABELS_ONLY or self._source_pixmap is None or self._source_pixmap.isNull():
            self._display_pixmap = None
            self._cached_lod = lod
            self.update()
            return
        if self._cached_lod == lod and self._display_pixmap is not None:
            return
        size = preview_pixel_size(lod)
        transform = (
            Qt.TransformationMode.SmoothTransformation
            if lod >= LOD_HIGH
            else Qt.TransformationMode.FastTransformation
        )
        self._display_pixmap = self._source_pixmap.scaled(
            size,
            size,
            Qt.AspectRatioMode.KeepAspectRatio,
            transform,
        )
        self._cached_lod = lod
        self.update()

    def set_preview_pixmap(self, pixmap) -> None:
        """Backward-compatible entry point."""
        if pixmap is None or pixmap.isNull():
            self.clear_preview()
            return
        self.set_preview_source(pixmap)

    def hoverEnterEvent(self, event) -> None:  # noqa: N802
        self._hovered = True
        self.update()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:  # noqa: N802
        self._hovered = False
        self.update()
        super().hoverLeaveEvent(event)

    def paint(self, painter: QPainter, option, widget=None) -> None:  # noqa: N802
        rect = self.boundingRect().adjusted(1, 1, -1, -1)
        if self._selected:
            painter.setPen(QPen(TILE_SELECTED, 2.5))
        elif self._hovered:
            painter.setPen(QPen(TILE_HOVER, 2))
        else:
            painter.setPen(QPen(TILE_BORDER, 1))

        show_preview = (
            self._display_lod > LOD_LABELS_ONLY
            and self._display_pixmap is not None
            and not self._display_pixmap.isNull()
        )
        if show_preview:
            painter.setBrush(QBrush(TILE_FILL))
            painter.drawRoundedRect(rect, 6, 6)
            target = rect.adjusted(3, 3, -3, -3)
            painter.drawPixmap(target.toRect(), self._display_pixmap)
        else:
            painter.setBrush(QBrush(TILE_FILL))
            painter.drawRoundedRect(rect, 6, 6)
