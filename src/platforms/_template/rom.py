"""{{platform_label}} ROM scan entry point."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from ...core.assets import Asset


def scan_{{platform_id}}_rom_path(
    path: str | Path,
    progress: Callable[[str], None] | None = None,
    **_kwargs,
) -> list[Asset]:
    raise NotImplementedError("Implement scan_{{platform_id}}_rom_path in platforms/{{platform_id}}/rom.py")
