"""Shared GLB material policy for DS model export and preview."""

from .apply import apply_glb_policy
from .classify import RenderClass, classify_material

__all__ = ["RenderClass", "apply_glb_policy", "classify_material"]
