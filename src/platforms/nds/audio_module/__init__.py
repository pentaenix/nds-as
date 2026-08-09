from __future__ import annotations

from ....core.modules.protocols import AudioModule
from ....core.modules.types import ExportRoute, PreviewContext, PreviewRoute
from ....scanner import Asset

_NDS_AUDIO_MAGICS = frozenset({"SDAT", "SWAR", "SWAV", "STRM", "SSEQ", "SSAR", "SBNK"})


class NdsAudioModule:
    platform_id = "nds"

    def preview_route(self, asset: Asset) -> PreviewRoute | None:
        if asset.magic in _NDS_AUDIO_MAGICS:
            return PreviewRoute.NDS_AUDIO
        return None

    def preview(self, ctx: PreviewContext, asset: Asset) -> bool:
        window = ctx.window
        window.preview.show_message(
            f"Audio asset selected. Use Export Selected… to write raw files and WAV previews where possible.\n\n{asset.virtual_path}"
        )
        window._update_status(f"Audio selected: {asset.virtual_path}")
        return True

    def export_route(self, asset: Asset) -> ExportRoute | None:
        if asset.magic in _NDS_AUDIO_MAGICS:
            return ExportRoute.NDS_STANDARD
        return None
