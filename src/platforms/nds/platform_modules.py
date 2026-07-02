from __future__ import annotations

from ...core.modules.registry import PlatformModules
from .audio_module import NdsAudioModule
from .details_module import NdsDetailsModule
from .model_module import NdsModelModule
from .scan_module import NdsScanModule
from .export_module import NdsExportModule
from .texture_module import NdsTextureModule


def build_nds_modules() -> PlatformModules:
    return PlatformModules(
        platform_id="nds",
        scan=NdsScanModule(),
        model=NdsModelModule(),
        texture=NdsTextureModule(),
        audio=NdsAudioModule(),
        details=NdsDetailsModule(),
        export=NdsExportModule(),
        toolkit=None,
    )
