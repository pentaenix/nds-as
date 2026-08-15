"""{{platform_label}} GLB post-process — platform-owned material policy.

Implement classification in ``platforms/{{platform_id}}/gltf/``.
Do **not** import a shared top-level ``glb_policy`` package — it does not exist.
"""
from __future__ import annotations

from pathlib import Path


def apply_platform_glb_policy(glb_path: Path) -> None:
    """Rewrite materials and ``extras.rae`` for this platform only."""
    _ = Path(glb_path)
    # Stub: classify materials and set extras.rae.platform = "{{platform_id}}".
