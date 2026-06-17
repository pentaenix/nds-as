from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


class ModelPreviewQuality(str, Enum):
    FULL_FIDELITY = "full_fidelity"
    BALANCED = "balanced"
    FAST_TEXTURED = "fast_textured"
    GEOMETRY_ONLY = "geometry_only"


@dataclass(frozen=True, slots=True)
class ModelPreviewPolicy:
    mode: ModelPreviewQuality
    label: str
    max_decoded_images: int | None
    max_candidate_archives: int | None
    allow_broad_fallback: bool
    allow_animation_frames: bool
    allow_palette_variants: bool
    exact_materials_only: bool

    @property
    def cache_key(self) -> str:
        return self.mode.value

    @property
    def full_fidelity(self) -> bool:
        return self.mode == ModelPreviewQuality.FULL_FIDELITY

    @property
    def geometry_only(self) -> bool:
        return self.mode == ModelPreviewQuality.GEOMETRY_ONLY

    def summary(self) -> str:
        if self.full_fidelity:
            return "current exhaustive texture behavior"
        if self.geometry_only:
            return "no texture resolution"
        limit = "unlimited" if self.max_decoded_images is None else str(self.max_decoded_images)
        return f"decode cap {limit}; broad fallback {'on' if self.allow_broad_fallback else 'off'}"


_POLICY_BY_MODE: dict[ModelPreviewQuality, ModelPreviewPolicy] = {
    ModelPreviewQuality.FULL_FIDELITY: ModelPreviewPolicy(
        mode=ModelPreviewQuality.FULL_FIDELITY,
        label="Full Fidelity",
        max_decoded_images=None,
        max_candidate_archives=None,
        allow_broad_fallback=True,
        allow_animation_frames=True,
        allow_palette_variants=True,
        exact_materials_only=False,
    ),
    ModelPreviewQuality.BALANCED: ModelPreviewPolicy(
        mode=ModelPreviewQuality.BALANCED,
        label="Balanced",
        max_decoded_images=512,
        max_candidate_archives=32,
        allow_broad_fallback=True,
        allow_animation_frames=True,
        allow_palette_variants=True,
        exact_materials_only=False,
    ),
    ModelPreviewQuality.FAST_TEXTURED: ModelPreviewPolicy(
        mode=ModelPreviewQuality.FAST_TEXTURED,
        label="Fast Textured",
        max_decoded_images=128,
        max_candidate_archives=8,
        allow_broad_fallback=False,
        allow_animation_frames=False,
        allow_palette_variants=False,
        exact_materials_only=True,
    ),
    ModelPreviewQuality.GEOMETRY_ONLY: ModelPreviewPolicy(
        mode=ModelPreviewQuality.GEOMETRY_ONLY,
        label="Geometry Only",
        max_decoded_images=0,
        max_candidate_archives=0,
        allow_broad_fallback=False,
        allow_animation_frames=False,
        allow_palette_variants=False,
        exact_materials_only=True,
    ),
}

MODEL_PREVIEW_QUALITY_CHOICES: tuple[tuple[ModelPreviewQuality, str], ...] = tuple(
    (quality, _POLICY_BY_MODE[quality].label)
    for quality in (
        ModelPreviewQuality.FULL_FIDELITY,
        ModelPreviewQuality.BALANCED,
        ModelPreviewQuality.FAST_TEXTURED,
        ModelPreviewQuality.GEOMETRY_ONLY,
    )
)


def model_preview_policy(value: ModelPreviewPolicy | ModelPreviewQuality | str | None = None) -> ModelPreviewPolicy:
    if isinstance(value, ModelPreviewPolicy):
        return value
    if value is None:
        return _POLICY_BY_MODE[ModelPreviewQuality.FULL_FIDELITY]
    if isinstance(value, ModelPreviewQuality):
        return _POLICY_BY_MODE[value]
    try:
        return _POLICY_BY_MODE[ModelPreviewQuality(str(value))]
    except Exception:
        return _POLICY_BY_MODE[ModelPreviewQuality.FULL_FIDELITY]


def preview_quality_labels() -> Iterable[str]:
    for _quality, label in MODEL_PREVIEW_QUALITY_CHOICES:
        yield label
