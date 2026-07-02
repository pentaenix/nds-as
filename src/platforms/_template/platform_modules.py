from __future__ import annotations

from ...core.modules.registry import PlatformModules
from .audio_module import {{platform_class}}AudioModule
from .details_module import {{platform_class}}DetailsModule
from .export_module import {{platform_class}}ExportModule
from .model_module import {{platform_class}}ModelModule
from .scan_module import {{platform_class}}ScanModule
from .texture_module import {{platform_class}}TextureModule


def build_{{platform_id}}_modules() -> PlatformModules:
    return PlatformModules(
        platform_id="{{platform_id}}",
        scan={{platform_class}}ScanModule(),
        model={{platform_class}}ModelModule(),
        texture={{platform_class}}TextureModule(),
        audio={{platform_class}}AudioModule(),
        details={{platform_class}}DetailsModule(),
        export={{platform_class}}ExportModule(),
        toolkit=None,
    )
