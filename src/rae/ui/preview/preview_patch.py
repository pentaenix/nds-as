"""Build patched preview GLBs for the WebEngine viewer."""
from __future__ import annotations

from pathlib import Path

from ...glb_policy.texture_patch import write_flipbook_preview_glbs, write_patched_preview_glb


class PreviewGlbPatcher:
    """Keeps patched preview GLBs beside the apicula output folder."""

    def __init__(self) -> None:
        self._preview_counter = 0

    def build_preview_glb(
        self,
        source_glb: Path,
        *,
        mesh_labels: list[str],
        mesh_texture_paths: list[Path | None],
    ) -> Path:
        source_glb = source_glb.resolve()
        preview_dir = source_glb.parent / ".rae_preview"
        preview_dir.mkdir(parents=True, exist_ok=True)
        self._preview_counter += 1
        out_path = preview_dir / f"textured_{self._preview_counter:05d}.glb"
        return write_patched_preview_glb(
            source_glb,
            out_path,
            mesh_labels=mesh_labels,
            mesh_texture_paths=mesh_texture_paths,
        )

    def build_flipbook_glbs(
        self,
        source_glb: Path,
        *,
        material_name: str,
        frame_paths: list[Path],
        mesh_labels: list[str],
        base_mesh_paths: list[Path | None],
    ) -> list[Path]:
        source_glb = source_glb.resolve()
        preview_dir = source_glb.parent / ".rae_preview" / "flipbook" / material_name.casefold()
        return write_flipbook_preview_glbs(
            source_glb,
            preview_dir,
            material_name=material_name,
            frame_paths=frame_paths,
            base_mesh_paths=base_mesh_paths,
            mesh_labels=mesh_labels,
        )
