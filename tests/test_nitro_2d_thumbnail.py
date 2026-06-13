"""Nitro 2D thumbnail preview helpers."""
from __future__ import annotations

from PIL import Image

from rae.platforms.nds.nitro_2d import tight_crop_pil


def test_tight_crop_trims_transparent_padding() -> None:
    canvas = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    sprite = Image.new("RGBA", (20, 30), (255, 0, 0, 255))
    canvas.paste(sprite, (40, 35))
    cropped = tight_crop_pil(canvas)
    assert cropped.size == (20, 30)
