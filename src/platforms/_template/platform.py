"""{{platform_label}} platform registration."""
from __future__ import annotations

from ...core.registry import Platform
from .rom import scan_{{platform_id}}_rom_path


{{platform_class}}_PLATFORM = Platform(
    id="{{platform_id}}",
    label="{{platform_label}}",
    rom_extensions={{rom_extensions}},
    status="{{platform_status}}",
    scan_rom_path=scan_{{platform_id}}_rom_path if "{{platform_status}}" == "active" else None,
)
