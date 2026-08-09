"""Thin entry point for the Windows ISO-owned GLB policy."""
from __future__ import annotations

from .gltf.apply import apply_platform_glb_policy

__all__ = ["apply_platform_glb_policy"]
