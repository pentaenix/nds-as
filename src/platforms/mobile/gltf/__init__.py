"""mobile-owned glTF/GLB helpers (duplicated per island — no shared top-level glb_policy)."""
from .apply import apply_glb_policy, apply_glb_policy as apply_platform_glb_policy
from .classify import RenderClass, classify_material

__all__ = [
    "RenderClass",
    "apply_glb_policy",
    "apply_platform_glb_policy",
    "classify_material",
]
