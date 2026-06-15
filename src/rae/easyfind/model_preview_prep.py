"""Backward-compatible re-export; use rae.model_preview.pipeline instead."""
from ..model_preview.pipeline import ModelPreviewBundle as TexturedGlbPreview
from ..model_preview.pipeline import prepare_model_preview as prepare_bmd0_textured_glb

__all__ = ["TexturedGlbPreview", "prepare_bmd0_textured_glb"]
