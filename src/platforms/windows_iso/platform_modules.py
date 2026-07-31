from __future__ import annotations

from ...core.modules.registry import PlatformModules
from .audio_module import WindowsIsoAudioModule
from .details_module import WindowsIsoDetailsModule
from .export_module import WindowsIsoExportModule
from .model_module import WindowsIsoModelModule
from .scan_module import WindowsIsoScanModule
from .texture_module import WindowsIsoTextureModule


def build_windows_iso_modules() -> PlatformModules:
    return PlatformModules(
        platform_id="windows_iso",
        scan=WindowsIsoScanModule(),
        model=WindowsIsoModelModule(),
        texture=WindowsIsoTextureModule(),
        audio=WindowsIsoAudioModule(),
        details=WindowsIsoDetailsModule(),
        export=WindowsIsoExportModule(),
        toolkit=None,
    )
