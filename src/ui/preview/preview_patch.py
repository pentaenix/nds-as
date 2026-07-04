"""Build patched preview GLBs for the WebEngine viewer (via PlatformDispatch)."""
from __future__ import annotations

from pathlib import Path

from ...core.modules.dispatch import PlatformDispatch


class PreviewGlbPatcher:
    """UI-facing wrapper; NDS island patcher runs behind PlatformDispatch."""

    def build_preview_glb(
        self,
        source_glb: Path,
        *,
        mesh_labels: list[str],
        mesh_texture_paths: list[Path | None],
        texture_by_name: dict[str, Path] | None = None,
        material_to_texture: dict[str, str] | None = None,
        stage_texture_paths: list[Path] | None = None,
    ) -> Path:
        result = PlatformDispatch.build_web_preview_glb(
            source_glb,
            rom_platform_id="nds",
            mesh_labels=mesh_labels,
            mesh_texture_paths=mesh_texture_paths,
            texture_by_name=texture_by_name,
            material_to_texture=material_to_texture,
            stage_texture_paths=stage_texture_paths,
        )
        if result is None:
            return source_glb.resolve()
        return result

    def build_flipbook_glbs(
        self,
        source_glb: Path,
        *,
        material_name: str,
        frame_paths: list[Path],
        mesh_labels: list[str],
        base_mesh_paths: list[Path | None],
    ) -> list[Path]:
        return PlatformDispatch.build_flipbook_preview_glbs(
            source_glb,
            rom_platform_id="nds",
            material_name=material_name,
            frame_paths=frame_paths,
            mesh_labels=mesh_labels,
            base_mesh_paths=base_mesh_paths,
        )
