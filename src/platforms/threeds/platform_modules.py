from __future__ import annotations

from ...core.modules.registry import PlatformModules
from .audio_module import ThreedsAudioModule
from .details_module import ThreedsDetailsModule
from .export_module import ThreedsExportModule
from .model_module import ThreedsModelModule
from .scan_module import ThreedsScanModule
from .texture_module import ThreedsTextureModule


def build_threeds_modules() -> PlatformModules:
    return PlatformModules(
        platform_id="3ds",
        scan=ThreedsScanModule(),
        model=ThreedsModelModule(),
        texture=ThreedsTextureModule(),
        audio=ThreedsAudioModule(),
        details=ThreedsDetailsModule(),
        export=ThreedsExportModule(),
        toolkit=None,
    )
