import io
from pathlib import Path

import numpy as np
import trimesh
from PIL import Image

from rae.easyfind.model_thumbnail import render_glb_orthographic_thumbnail
from rae.model_preview.scene_snapshot import _rgba_float_to_u8


def test_rgba_float_to_u8_clamps_out_of_range():
    assert _rgba_float_to_u8(np.array([-1.05, 0.2, 0.3, 1.0])) == (0, 51, 76, 255)
    assert _rgba_float_to_u8(np.array([300, 128, 64, 255])) == (255, 128, 64, 255)


def test_render_glb_orthographic_thumbnail_writes_png(tmp_path: Path):
    glb = tmp_path / "box.glb"
    trimesh.creation.box().export(glb)
    png_bytes, width, height = render_glb_orthographic_thumbnail(glb, width=96, height=96)
    assert png_bytes
    assert png_bytes.startswith(b"\x89PNG")
    assert width == 96
    assert height == 96
    image = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    assert max(image.getextrema()[3]) > 0
