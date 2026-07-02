from __future__ import annotations

from pathlib import Path

from ....core.mapping import choose_mapping, load_mappings, mapping_summary
from ....core.modules.protocols import ScanModule
from ....core.modules.types import ProfileSummary, Progress
from ....scanner import Asset
from ..profiles import detect_profile
from ..rom import NDSRom
from ..scanner import scan_nds_path
from ..virtual_texture_assets import expand_btx0_texture_slots


class NdsScanModule:
    platform_id = "nds"

    def scan_rom(
        self,
        path: str | Path,
        *,
        deep_scan: bool = False,
        progress: Progress | None = None,
    ) -> list[Asset]:
        scan_mode = "exhaustive" if deep_scan else "guided"
        if progress:
            if deep_scan:
                progress.emit(
                    "Exhaustive scan: reading ROM filesystem, known containers, compressed streams, and carved Nitro files."
                )
            else:
                progress(
                    "Guided scan: reading ROM filesystem and known containers, "
                    "with bounded mapping-priority carving for likely model/texture archives."
                )
        return scan_nds_path(
            path,
            progress=progress,
            carve_unknown_blobs=deep_scan,
            expand_audio_archives=False,
            scan_mode=scan_mode,
        )

    def post_scan(self, assets: list[Asset]) -> list[Asset]:
        return expand_btx0_texture_slots(assets)

    def load_profile(self, source_path: Path) -> ProfileSummary:
        rom = NDSRom.from_path(str(source_path))
        rom_game_code = (rom.info.game_code or "").strip().upper()[:4]
        rom_title = (rom.info.title or "").strip()
        files = list(rom.iter_files())
        profile = detect_profile(rom.info.title, rom.info.game_code, [f.path for f in files])
        mapping = choose_mapping(
            rom.info.title,
            rom.info.game_code,
            available=load_mappings(platform="nds"),
        )
        parts = [f"Profile: {profile.label} ({profile.confidence}).", mapping_summary(mapping)]
        if profile.priority_queries:
            parts.append("Useful searches: " + ", ".join(profile.priority_queries) + ".")
        if profile.priority_paths:
            parts.append("Priority paths: " + ", ".join(profile.priority_paths[:8]) + ".")
        return ProfileSummary(
            rom_game_code=rom_game_code,
            rom_title=rom_title,
            profile_text="\n".join(parts),
            current_mapping=mapping,
        )

    def supports_texture_library_warmup(self) -> bool:
        return True
