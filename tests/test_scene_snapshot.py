from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
from PIL import Image

from rae.model_preview.scene_snapshot import (
    _DrawMesh,
    _render_draw_meshes,
    expand_textured_corners,
    sample_texture_nearest,
)
from rae.platforms.nds.gltf.preview_textures import MaterialPreviewState


def _triangle(z: float, rgba: tuple[float, float, float, float], mode: str = "opaque") -> _DrawMesh:
    vertices = np.asarray([[-1.0, -1.0, z], [1.0, -1.0, z], [0.0, 1.0, z]], dtype=float)
    return _DrawMesh(
        vertices=vertices,
        faces=np.asarray([[0, 1, 2]], dtype=int),
        blend_mode=mode,
        center=np.mean(vertices, axis=0),
        colors=np.tile(np.asarray(rgba, dtype=float), (3, 1)),
    )


def _center_pixel(meshes: list[_DrawMesh]) -> tuple[int, int, int, int]:
    png, _, _ = _render_draw_meshes(
        meshes,
        width=64,
        height=64,
        yaw_deg=0.0,
        pitch_deg=0.0,
        zoom_factor=1.0,
    )
    assert png is not None
    with Image.open(io.BytesIO(png)) as image:
        return image.convert("RGBA").getpixel((32, 32))


def test_snapshot_depth_keeps_surface_closest_to_camera() -> None:
    pixel = _center_pixel([
        _triangle(-0.5, (1.0, 0.0, 0.0, 1.0)),
        _triangle(0.5, (0.0, 0.0, 1.0, 1.0)),
    ])

    assert pixel[:3] == (0, 0, 255)


def test_snapshot_alpha_composites_translucent_surface_over_lower_layer() -> None:
    pixel = _center_pixel([
        _triangle(-0.5, (1.0, 0.0, 0.0, 1.0)),
        _triangle(0.5, (0.0, 0.0, 1.0, 0.5), "blend"),
    ])

    assert pixel[0] in range(126, 130)
    assert pixel[1] == 0
    assert pixel[2] in range(126, 130)
    assert pixel[3] == 255


def test_expanded_texture_coordinates_preserve_repeating_uv_spans() -> None:
    mesh = SimpleNamespace(
        vertices=np.asarray([[0.0, 0.0, 0.0], [3.0, 0.0, 0.0], [0.0, 0.0, 2.0]]),
        faces=np.asarray([[0, 1, 2]], dtype=int),
        visual=SimpleNamespace(
            kind="texture",
            uv=np.asarray([[0.0, 0.0], [3.0, 0.0], [0.0, 2.0]], dtype=float),
        ),
    )

    expanded = expand_textured_corners(mesh, mesh.vertices, mesh.faces)

    assert expanded is not None
    _vertices, texcoords, _faces = expanded
    assert texcoords.tolist() == [[0.0, 1.0], [3.0, 1.0], [0.0, -1.0]]


def test_nearest_texture_sampler_applies_mask_per_texel() -> None:
    texture = np.asarray([[[255, 128, 64, 255], [255, 128, 64, 0]]], dtype=np.uint8)
    state = MaterialPreviewState(alpha_mode="MASK", alpha_cutoff=0.5)

    assert sample_texture_nearest(texture, 0.0, 0.0, state) == (255, 128, 64, 255)
    assert sample_texture_nearest(texture, 0.99, 0.0, state) == (255, 128, 64, 0)
