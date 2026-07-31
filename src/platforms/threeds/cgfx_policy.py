"""Conservative GLB material policy for generic NintendoWare CGFX assets."""
from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from .gltf.glb_io import material_texture_bytes, read_glb


def _alpha_mode(texture: bytes | None) -> tuple[str, float | None]:
    if not texture:
        return "OPAQUE", None
    try:
        with Image.open(io.BytesIO(texture)) as image:
            if "A" not in image.getbands():
                return "OPAQUE", None
            values = image.getchannel("A").getextrema()
            if not values or values[0] == 255:
                return "OPAQUE", None
            colors = image.getchannel("A").getcolors(maxcolors=257)
            if colors is not None and all(alpha in {0, 255} for _count, alpha in colors):
                return "MASK", 0.5
            return "BLEND", None
    except Exception:
        return "OPAQUE", None


def apply_cgfx_glb_policy(
    path: Path,
    *,
    game_id: str,
    animation_name: str | None = None,
) -> None:
    glb = read_glb(path)
    root_rae = glb.json.setdefault("extras", {}).setdefault("rae", {})
    root_rae.update({"platform": "3ds", "schemaVersion": 1, "game": game_id, "format": "cgfx"})
    if animation_name:
        animations = glb.json.get("animations") or []
        if animations and isinstance(animations[0], dict):
            # Assimp derives a Collada animation name from the first animated
            # channel.  Restore the authored CGFX clip name shown in RAE.
            animations[0]["name"] = animation_name
    for index, material in enumerate(glb.json.get("materials") or []):
        if not isinstance(material, dict):
            continue
        mode, cutoff = _alpha_mode(material_texture_bytes(glb, index))
        material["alphaMode"] = mode
        if cutoff is None:
            material.pop("alphaCutoff", None)
        else:
            material["alphaCutoff"] = cutoff
        # CGFX assets frequently use mirrored component meshes and authored
        # winding that differs between exporters. Preserve both faces here.
        material["doubleSided"] = True
        material.setdefault("extras", {}).setdefault("rae", {}).update(
            {"platform": "3ds", "schemaVersion": 1, "renderClass": mode.lower()}
        )
    glb.write(path)
