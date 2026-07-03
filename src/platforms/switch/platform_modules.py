from __future__ import annotations

from ...core.modules.registry import PlatformModules
from .audio_module import SwitchAudioModule
from .details_module import SwitchDetailsModule
from .export_module import SwitchExportModule
from .model_module import SwitchModelModule
from .scan_module import SwitchScanModule
from .texture_module import SwitchTextureModule


def build_switch_modules() -> PlatformModules:
    return PlatformModules(
        platform_id="switch",
        scan=SwitchScanModule(),
        model=SwitchModelModule(),
        texture=SwitchTextureModule(),
        audio=SwitchAudioModule(),
        details=SwitchDetailsModule(),
        export=SwitchExportModule(),
        toolkit=None,
    )
