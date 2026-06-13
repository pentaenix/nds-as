"""EasyFind canvas LOD tier tests."""
from __future__ import annotations

from rae.ui.easyfind.canvas_lod import (
    LOD_HIGH,
    LOD_LABELS_ONLY,
    LOD_LOW,
    lod_for_zoom,
    preview_pixel_size,
)


def test_lod_for_zoom_tiers() -> None:
    assert lod_for_zoom(0.05) == LOD_LABELS_ONLY
    assert lod_for_zoom(0.20) == LOD_LOW
    assert lod_for_zoom(0.50) == LOD_HIGH


def test_preview_pixel_size_matches_lod() -> None:
    assert preview_pixel_size(LOD_LABELS_ONLY) == 0
    assert preview_pixel_size(LOD_LOW) == 40
    assert preview_pixel_size(LOD_HIGH) == 96
