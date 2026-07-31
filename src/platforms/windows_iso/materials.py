"""Material classification for Marine Park Empire texture exports."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image


def _sample_face_alpha(
    alpha: np.ndarray,
    uvs: Sequence[Sequence[float]] | np.ndarray,
    faces: Sequence[Sequence[int]] | np.ndarray,
) -> np.ndarray:
    """Sample the texture region actually used by one material's faces."""
    uv_array = np.asarray(uvs, dtype=np.float64)
    face_array = np.asarray(faces, dtype=np.int64)
    if (
        uv_array.ndim != 2
        or uv_array.shape[1] != 2
        or face_array.ndim != 2
        or face_array.shape[1] != 3
        or not face_array.size
        or face_array.min() < 0
        or face_array.max() >= len(uv_array)
    ):
        return np.asarray([], dtype=np.uint8)
    # Vertices, edge midpoints, and centroid reliably distinguish the broad
    # texture regions used by V3D's opaque and translucent mesh sections.
    barycentric = np.asarray(
        (
            (1.0, 0.0, 0.0),
            (0.0, 1.0, 0.0),
            (0.0, 0.0, 1.0),
            (0.5, 0.5, 0.0),
            (0.5, 0.0, 0.5),
            (0.0, 0.5, 0.5),
            (1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0),
        ),
        dtype=np.float64,
    )
    points = np.einsum("kc,fcd->fkd", barycentric, uv_array[face_array])
    wrapped = np.mod(points, 1.0)
    height, width = alpha.shape
    x = np.minimum((wrapped[..., 0] * width).astype(np.int64), width - 1)
    y = np.minimum((wrapped[..., 1] * height).astype(np.int64), height - 1)
    return alpha[y, x].reshape(-1)


def gltf_alpha_properties(
    texture_png: Path | None,
    *,
    uvs: Sequence[Sequence[float]] | np.ndarray | None = None,
    faces: Sequence[Sequence[int]] | np.ndarray | None = None,
) -> dict[str, object]:
    """Return the least expensive glTF alpha mode supported by the texture.

    The game uses many opaque DDS images and binary foliage/fence cutouts.  A
    blanket BLEND mode disables reliable depth ordering in WebGL, so only
    genuinely graduated alpha is treated as translucent.
    """
    if texture_png is None:
        return {}
    with Image.open(texture_png) as image:
        if "A" not in image.getbands():
            return {}
        alpha = np.asarray(image.getchannel("A"), dtype=np.uint8)
    if alpha.size == 0:
        return {}
    source_low = int(alpha.min())
    source_high = int(alpha.max())
    # Fully opaque is ordinary OPAQUE. Some legacy DDS files contain an unused
    # constant-zero alpha plane; treating that plane as transparency would make
    # an otherwise valid model disappear.
    if source_low == source_high:
        return {}
    samples = (
        _sample_face_alpha(alpha, uvs, faces)
        if uvs is not None and faces is not None
        else np.asarray([], dtype=np.uint8)
    )
    considered = samples if samples.size else alpha.reshape(-1)
    low = int(considered.min())
    high = int(considered.max())
    if low == high:
        if high >= 247:
            return {}
        if low <= 8:
            return {"alphaMode": "MASK", "alphaCutoff": 0.5}
        return {"alphaMode": "BLEND"}
    opaque = np.count_nonzero(considered >= 247) / considered.size
    # Several animal DDS files use the alpha channel as softly compressed
    # surface data rather than transparency. If every sampled texel remains
    # above the mask threshold and at least three quarters are fully opaque,
    # BLEND only exposes internal faces and attachment seams as apparent holes.
    if low >= 128 and opaque >= 0.75:
        return {}
    middle = np.count_nonzero((considered > 8) & (considered < 247))
    # DXT interpolation introduces a small halo of intermediate values around
    # otherwise binary masks. Treating that compression noise as BLEND makes
    # whole animals participate in unstable transparent-object sorting.
    if middle / considered.size <= 0.05:
        # A mostly opaque region whose DXT alpha never approaches the cutoff is
        # visually opaque. Avoid putting that entire mesh into the blend queue.
        if low >= 128:
            return {}
        return {"alphaMode": "MASK", "alphaCutoff": 0.5}
    return {"alphaMode": "BLEND"}
