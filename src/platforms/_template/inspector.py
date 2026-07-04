"""{{platform_label}} inspector UI hooks — register tabs/commands for this island only."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def register_inspector(window: object, *, glb_path: Path, asset_id: str) -> None:
    """Attach platform-specific inspector tabs to *window* for the loaded GLB."""
    _ = (window, glb_path, asset_id)
