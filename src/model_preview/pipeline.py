"""Backward-compatible re-export; NDS pipeline lives in platforms/nds/model_module."""
from ..platforms.nds.model_module.preview_pipeline import (  # noqa: F401
    ModelPreviewBundle,
    prepare_model_preview,
)

__all__ = ["ModelPreviewBundle", "prepare_model_preview"]
