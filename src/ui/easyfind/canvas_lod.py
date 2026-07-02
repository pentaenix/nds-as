"""Zoom level-of-detail tiers for EasyFind canvas previews."""
from __future__ import annotations

# Scene zoom (QGraphicsView transform m11).
LOD_ZOOM_LABELS_ONLY = 0.04
LOD_ZOOM_FULL = 0.28

PREVIEW_PX_LOW = 40
PREVIEW_PX_HIGH = 96

LOD_LABELS_ONLY = 0
LOD_LOW = 1
LOD_HIGH = 2


def lod_for_zoom(zoom: float) -> int:
    if zoom < LOD_ZOOM_LABELS_ONLY:
        return LOD_LABELS_ONLY
    if zoom < LOD_ZOOM_FULL:
        return LOD_LOW
    return LOD_HIGH


def preview_pixel_size(lod: int) -> int:
    if lod >= LOD_HIGH:
        return PREVIEW_PX_HIGH
    if lod >= LOD_LOW:
        return PREVIEW_PX_LOW
    return 0
