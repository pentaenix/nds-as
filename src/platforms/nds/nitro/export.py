"""PNG export helpers for decoded Nitro textures."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from ....core.util import sanitize_component
from .types import DecodedImage, Image, ImageDraw

def save_decoded_images(images: Iterable[DecodedImage], out_dir: str | Path, *, prefix: str = "texture") -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    used: dict[str, int] = {}
    for image in images:
        stem = sanitize_component(image.name or prefix)
        index = used.get(stem, 0)
        used[stem] = index + 1
        if index:
            stem = f"{stem}_{index:02d}"
        path = out / f"{stem}.png"
        image.save_png(path)
        written.append(path)
    return written


def make_contact_sheet(images: list[DecodedImage], *, max_thumb: int = 128, columns: int = 4):
    if Image is None:
        raise RuntimeError("Pillow is required to build contact sheets. Install requirements.txt.")
    if not images:
        return None
    thumbs = []
    for item in images:
        im = item.to_pil()
        im.thumbnail((max_thumb, max_thumb), Image.Resampling.NEAREST)
        thumbs.append((item, im.copy()))
    label_h = 22
    cell_w = max(max_thumb, 96)
    cell_h = max_thumb + label_h
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGBA", (columns * cell_w, rows * cell_h), (32, 32, 32, 255))
    draw = ImageDraw.Draw(sheet) if ImageDraw else None
    for idx, (item, im) in enumerate(thumbs):
        x = (idx % columns) * cell_w
        y = (idx // columns) * cell_h
        ox = x + (cell_w - im.width) // 2
        oy = y + (max_thumb - im.height) // 2
        sheet.alpha_composite(im, (ox, oy))
        if draw:
            label = f"{item.name[:15]} {item.width}x{item.height} f{item.format_id}"
            draw.text((x + 4, y + max_thumb + 3), label, fill=(235, 235, 235, 255))
    return sheet

