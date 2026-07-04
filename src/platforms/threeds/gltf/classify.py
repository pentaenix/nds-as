"""Material render-class classification for DS-derived GLBs."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .geometry_stats import MaterialGeometryStats, is_predominantly_horizontal
from .texture_alpha import (
    texture_has_fully_transparent_pixels,
    texture_has_meaningful_alpha,
    texture_has_partial_alpha_channel,
    texture_has_soft_edge_alpha_only,
)

SCHEMA_VERSION = 1
ALPHA_CUTOFF = 0.5


class RenderClass(str, Enum):
    OPAQUE = "opaque"
    MASK = "mask"
    BLEND = "blend"
    UNIFORM_DECAL = "uniform_decal"
    ADDITIVE = "additive"


@dataclass(frozen=True)
class ClassificationResult:
    render_class: RenderClass
    texture_meaningful_alpha: bool
    horizontal_face_fraction: float
    nitro_alpha: float


def _material_alpha(material: dict) -> float:
    pbr = material.get("pbrMetallicRoughness") or {}
    factor = pbr.get("baseColorFactor") or [1.0, 1.0, 1.0, 1.0]
    try:
        if isinstance(factor, (list, tuple)) and len(factor) >= 4:
            return float(factor[3])
    except (TypeError, ValueError):
        pass
    extras = material.get("extras") or {}
    nitro = (extras.get("rae") or {}).get("nitro") or {}
    try:
        return float(nitro.get("alpha", 1.0))
    except (TypeError, ValueError):
        return 1.0


def _nitro_alpha(material: dict) -> float:
    extras = material.get("extras") or {}
    nitro = (extras.get("rae") or {}).get("nitro") or {}
    try:
        return float(nitro.get("alpha", _material_alpha(material)))
    except (TypeError, ValueError):
        return _material_alpha(material)


def _nitro_cull_backface(material: dict) -> bool:
    extras = material.get("extras") or {}
    nitro = (extras.get("rae") or {}).get("nitro") or {}
    if "cullBackface" in nitro:
        return bool(nitro["cullBackface"])
    return not bool(material.get("doubleSided", False))


def _declared_alpha_mode(material: dict) -> str:
    return str(material.get("alphaMode") or "OPAQUE").upper()


def _nitro_texture_alpha_kind(material: dict) -> str:
    extras = material.get("extras") or {}
    nitro = (extras.get("rae") or {}).get("nitro") or {}
    return str(nitro.get("textureAlpha") or "opaque").lower()


def _nitro_blend_mode(material: dict) -> str:
    extras = material.get("extras") or {}
    nitro = (extras.get("rae") or {}).get("nitro") or {}
    return str(nitro.get("blendMode") or "").lower()


def _nitro_translucent_texture(material: dict) -> bool:
    """True when apicula marked BLEND from texture format (not material alpha alone)."""
    extras = material.get("extras") or {}
    nitro = (extras.get("rae") or {}).get("nitro") or {}
    if "textureAlpha" in nitro:
        return str(nitro["textureAlpha"]).lower() == "translucent"
    # Without nitro metadata, infer: BLEND declared but uniform material alpha means format-driven.
    alpha = _material_alpha(material)
    mode = _declared_alpha_mode(material)
    return mode == "BLEND" and alpha >= 0.999


def classify_material(
    material: dict,
    *,
    texture_bytes: bytes | None = None,
    geometry_stats: MaterialGeometryStats | None = None,
) -> ClassificationResult:
    nitro_alpha = _nitro_alpha(material)
    meaningful_alpha = texture_has_meaningful_alpha(texture_bytes)
    horizontal_fraction = geometry_stats.horizontal_face_fraction if geometry_stats else 0.0
    declared_mode = _declared_alpha_mode(material)
    nitro_tex_alpha = _nitro_texture_alpha_kind(material)

    if _nitro_blend_mode(material) == "additive":
        return ClassificationResult(
            render_class=RenderClass.ADDITIVE,
            texture_meaningful_alpha=meaningful_alpha,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    if nitro_alpha <= 0.0:
        return ClassificationResult(
            render_class=RenderClass.MASK,
            texture_meaningful_alpha=meaningful_alpha,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    if meaningful_alpha:
        if not texture_has_fully_transparent_pixels(texture_bytes):
            # ETC1A4 often varies alpha without any invisible texels — render solid.
            return ClassificationResult(
                render_class=RenderClass.OPAQUE,
                texture_meaningful_alpha=False,
                horizontal_face_fraction=horizontal_fraction,
                nitro_alpha=nitro_alpha,
            )
        if texture_has_partial_alpha_channel(texture_bytes):
            if texture_has_soft_edge_alpha_only(texture_bytes):
                return ClassificationResult(
                    render_class=RenderClass.MASK,
                    texture_meaningful_alpha=True,
                    horizontal_face_fraction=horizontal_fraction,
                    nitro_alpha=nitro_alpha,
                )
            return ClassificationResult(
                render_class=RenderClass.BLEND,
                texture_meaningful_alpha=True,
                horizontal_face_fraction=horizontal_fraction,
                nitro_alpha=nitro_alpha,
            )
        return ClassificationResult(
            render_class=RenderClass.MASK,
            texture_meaningful_alpha=True,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    if _nitro_translucent_texture(material):
        return ClassificationResult(
            render_class=RenderClass.BLEND,
            texture_meaningful_alpha=False,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    # Nitro color0-transparent textures: apicula marks MASK and writes alpha=0 texels,
    # but palette index 0 RGB is often white/tan. Downgrading to OPAQUE shows those as
    # solid white patches instead of holes.
    if nitro_tex_alpha == "transparent" or declared_mode == "MASK":
        return ClassificationResult(
            render_class=RenderClass.MASK,
            texture_meaningful_alpha=meaningful_alpha,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    if 0.0 < nitro_alpha < 1.0:
        if is_predominantly_horizontal(geometry_stats):
            return ClassificationResult(
                render_class=RenderClass.UNIFORM_DECAL,
                texture_meaningful_alpha=False,
                horizontal_face_fraction=horizontal_fraction,
                nitro_alpha=nitro_alpha,
            )
        return ClassificationResult(
            render_class=RenderClass.BLEND,
            texture_meaningful_alpha=False,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    if declared_mode == "BLEND" and nitro_tex_alpha == "opaque" and not meaningful_alpha:
        return ClassificationResult(
            render_class=RenderClass.OPAQUE,
            texture_meaningful_alpha=False,
            horizontal_face_fraction=horizontal_fraction,
            nitro_alpha=nitro_alpha,
        )

    return ClassificationResult(
        render_class=RenderClass.OPAQUE,
        texture_meaningful_alpha=False,
        horizontal_face_fraction=horizontal_fraction,
        nitro_alpha=nitro_alpha,
    )


def apply_render_class_to_material(
    material: dict,
    result: ClassificationResult,
) -> None:
    render_class = result.render_class
    cull_backface = _nitro_cull_backface(material)

    if render_class == RenderClass.OPAQUE:
        material.pop("alphaMode", None)
        material.pop("alphaCutoff", None)
        material["doubleSided"] = not cull_backface
    elif render_class == RenderClass.MASK:
        material["alphaMode"] = "MASK"
        material["alphaCutoff"] = ALPHA_CUTOFF
        material.pop("doubleSided", None)
        if cull_backface:
            material.pop("doubleSided", None)
        else:
            material["doubleSided"] = True
    elif render_class == RenderClass.BLEND:
        material["alphaMode"] = "BLEND"
        material.pop("alphaCutoff", None)
        material["doubleSided"] = not cull_backface
    elif render_class == RenderClass.UNIFORM_DECAL:
        material["alphaMode"] = "BLEND"
        material.pop("alphaCutoff", None)
        material["doubleSided"] = True
    elif render_class == RenderClass.ADDITIVE:
        material["alphaMode"] = "BLEND"
        material.pop("alphaCutoff", None)
        material["doubleSided"] = True

    extras: dict[str, Any] = dict(material.get("extras") or {})
    rae: dict[str, Any] = dict(extras.get("rae") or {})
    rae["schemaVersion"] = SCHEMA_VERSION
    rae["platform"] = "3ds"
    rae["renderClass"] = render_class.value
    rae["signals"] = {
        "textureMeaningfulAlpha": result.texture_meaningful_alpha,
        "horizontalFaceFraction": round(result.horizontal_face_fraction, 4),
    }
    nitro = dict(rae.get("nitro") or {})
    nitro.setdefault("alpha", result.nitro_alpha)
    nitro.setdefault("cullBackface", cull_backface)
    nitro.setdefault("cullFrontface", False)
    rae["nitro"] = nitro
    extras["rae"] = rae
    material["extras"] = extras
