from __future__ import annotations

from ...core.modules.registry import PlatformModules
from .audio_module import MobileAudioModule
from .details_module import MobileDetailsModule
from .model_module import MobileModelModule
from .scan_module import MobileScanModule
from .texture_module import MobileTextureModule
from .export_module import MobileExportModule
from .toolkit_module import MobileToolkitModule


def build_mobile_modules() -> PlatformModules:
    return PlatformModules(
        platform_id="mobile",
        scan=MobileScanModule(),
        model=MobileModelModule(),
        texture=MobileTextureModule(),
        audio=MobileAudioModule(),
        details=MobileDetailsModule(),
        export=MobileExportModule(),
        toolkit=MobileToolkitModule(),
    )
