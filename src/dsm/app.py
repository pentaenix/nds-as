from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .exporter import (
    apicula_available,
    apicula_help_text,
    archive_directory_as_zip,
    convert_texture_with_apicula,
    convert_with_apicula,
    converted_outputs,
    export_asset,
    export_assets,
    export_readable_asset,
    texture_outputs,
)
from .nitro_names import asset_browser_name, asset_filename_label, extract_nitro_names, texture_match_report
from .asset_resolver import build_related_assets, pokemon_path_texture_candidates, texture_matches_for_model
from .asset_graph import AssetGraph, build_asset_graph_for_selected, graph_to_manifest
from .session import save_session_zip, load_session_zip
from .nds import NDSRom
from .profiles import detect_profile
from .mapping import choose_mapping, mapping_summary
from .scanner import Asset, asset_search_text, filter_assets, filter_assets_by_types, filter_assets_indexed, scan_nds_path
from .nitro_textures import decode_btx_images, make_contact_sheet, save_decoded_images
from .texture_library import TextureLibrary, TextureLibraryStore
from .model_texture_resolver import resolve_model_textures, write_resolution_images
from .nitro_2d import decode_nitro2d_preview, decode_nitro2d_related_preview, save_preview_images
from .util import human_size
from .install import project_root

try:
    from PySide6.QtCore import Qt, QThread, Signal, QTimer, QEvent
    from PySide6.QtGui import QAction, QPixmap, QColor, QBrush, QGuiApplication, QPainter, QPalette, QSurfaceFormat, QWheelEvent
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QFileDialog,
        QHBoxLayout,
        QHeaderView,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QComboBox,
        QSplitter,
        QTableWidget,
        QTableWidgetItem,
        QTabWidget,
        QTextEdit,
        QTreeWidget,
        QTreeWidgetItem,
        QDialog,
        QDialogButtonBox,
        QRadioButton,
        QToolBar,
        QVBoxLayout,
        QWidget,
        QWidgetAction,
        QMenuBar,
        QMenu,
        QToolButton,
        QStyle,
        QFrame,
        QGridLayout,
    )
except Exception as exc:  # pragma: no cover - only used when UI deps are absent.
    raise SystemExit(
        "PySide6 is required for the desktop UI. Install requirements.txt first.\n"
        f"Original import error: {exc}"
    )


@dataclass(slots=True)
class TextureQuality:
    score: int
    mesh_faces: int
    texture_visuals: int
    image_count: int
    uv_sets: int
    sampled_unique_colors: int
    image_unique_colors: int
    flat_images: int

    @property
    def confident(self) -> bool:
        # A converted GLB can contain material slots or even placeholder image
        # objects without visibly texturing the model. Treat it as confirmed only
        # when the UV-sampled preview would actually have color variation.
        return self.mesh_faces > 0 and self.image_count > 0 and self.uv_sets > 0 and self.sampled_unique_colors >= 8 and self.score >= 55

    @property
    def weak_material_only(self) -> bool:
        return self.mesh_faces > 0 and self.score > 0 and not self.confident

    def summary(self) -> str:
        base = (
            f"texture quality {self.score}; mesh faces {self.mesh_faces}; "
            f"images {self.image_count}; UV sets {self.uv_sets}; "
            f"sampled colors {self.sampled_unique_colors}; image colors {self.image_unique_colors}"
        )
        if self.confident:
            return base + "; verified visible texture"
        if self.weak_material_only:
            return base + "; weak material evidence only"
        return base + "; no verified texture"


@dataclass(slots=True)
class CachedTextureResolution:
    report: str
    selected_texture_id: str
    preview_path: Path
    auxiliary_paths: list[Path]


class FilterWorker(QThread):
    finished_ok = Signal(int, list)
    failed = Signal(int, str)

    def __init__(
        self,
        generation: int,
        assets: list[Asset],
        *,
        enabled_types: list[str],
        mapping_query: str,
        text_query: str,
        search_text_by_id: dict[str, str],
    ):
        super().__init__()
        self.generation = generation
        self.assets = assets
        self.enabled_types = list(enabled_types)
        self.mapping_query = mapping_query
        self.text_query = text_query
        self.search_text_by_id = search_text_by_id

    def run(self) -> None:
        try:
            assets = self.assets
            if self.enabled_types:
                allowed = frozenset(self.enabled_types)
                assets = [asset for asset in assets if asset.magic in allowed]
            if self.mapping_query:
                assets = filter_assets_indexed(assets, self.mapping_query, self.search_text_by_id)
            if self.text_query:
                assets = filter_assets_indexed(assets, self.text_query, self.search_text_by_id)
            self.finished_ok.emit(self.generation, assets)
        except Exception as exc:
            self.failed.emit(self.generation, str(exc))


class TextureLibraryWarmupWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal()
    failed = Signal(str)

    def __init__(self, assets: list[Asset], store: TextureLibraryStore):
        super().__init__()
        self.assets = list(assets)
        self.store = store

    def run(self) -> None:
        try:
            if self.store.is_ready_for(self.assets):
                self.finished_ok.emit()
                return
            count, _digest = self.store.fingerprint(self.assets)
            self.progress.emit(f"Indexing texture dictionaries in background ({count:,} BTX0/BMD0 file(s))...")
            self.store.get_or_build(self.assets, progress=self.progress.emit)
            self.progress.emit(f"Texture dictionary index ready ({count:,} archive(s) indexed).")
            self.finished_ok.emit()
        except Exception as exc:
            self.failed.emit(str(exc))


class ScanWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(list, object)
    failed = Signal(str)

    def __init__(self, rom_path: str, *, deep_scan: bool = False):
        super().__init__()
        self.rom_path = rom_path
        self.deep_scan = deep_scan

    def run(self) -> None:
        try:
            self.progress.emit("Fast scan: reading ROM filesystem and known containers only. Relationship graph and mapped tree leaves are skipped during load.")
            assets = scan_nds_path(
                self.rom_path,
                progress=self.progress.emit,
                carve_unknown_blobs=self.deep_scan,
                expand_audio_archives=False,
            )
            self.progress.emit(f"Fast scan complete: {len(assets)} detected asset(s). Building visible folders lazily in the UI.")
            self.finished_ok.emit(assets, None)
        except Exception as exc:
            self.failed.emit(str(exc))


class RelationshipWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, assets: list[Asset], selected_ids: list[str], texture_library: TextureLibrary | None = None):
        super().__init__()
        self.assets = list(assets)
        self.selected_ids = list(selected_ids)
        self.texture_library = texture_library

    def run(self) -> None:
        try:
            graph = build_asset_graph_for_selected(
                self.assets,
                self.selected_ids,
                progress=self.progress.emit,
                texture_library=self.texture_library,
            )
            self.finished_ok.emit(graph)
        except Exception as exc:
            self.failed.emit(str(exc))




class SessionSaveWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, target: Path, assets: list[Asset], graph: AssetGraph, *, rom_path: str | None, profile_text: str, mapping_id: str, pinned_texture_asset_id: str | None):
        super().__init__()
        self.target = target
        self.assets = list(assets)
        self.graph = graph
        self.rom_path = rom_path
        self.profile_text = profile_text
        self.mapping_id = mapping_id
        self.pinned_texture_asset_id = pinned_texture_asset_id

    def run(self) -> None:
        try:
            written = save_session_zip(
                self.target,
                assets=self.assets,
                graph=self.graph,
                rom_path=self.rom_path,
                profile_text=self.profile_text,
                mapping_id=self.mapping_id,
                pinned_texture_asset_id=self.pinned_texture_asset_id,
                progress=self.progress.emit,
            )
            self.finished_ok.emit(str(written))
        except Exception as exc:
            self.failed.emit(str(exc))


class SessionLoadWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(object)
    failed = Signal(str)

    def __init__(self, source: Path):
        super().__init__()
        self.source = source

    def run(self) -> None:
        try:
            payload = load_session_zip(self.source, progress=self.progress.emit)
            self.finished_ok.emit(payload)
        except Exception as exc:
            self.failed.emit(str(exc))


def _write_btx_preview_images(texture_assets: list[Asset], out_dir: Path, *, max_textures: int = 2) -> list[Path]:
    """Decode candidate BTX0 files to PNGs for DSM's own preview fallback.

    This does not replace apicula output. It gives the preview widget actual
    bitmap data when the converted GLB has UV/material slots but no embedded
    image.
    """
    written: list[Path] = []
    target = out_dir / "dsm_decoded_textures"
    for texture in texture_assets[:max_textures]:
        try:
            images = decode_btx_images(texture.data, max_images=16, mode="all-palettes")
            if not images:
                continue
            written.extend(save_decoded_images(images, target / texture.asset_id, prefix=Path(texture.virtual_path).stem))
        except Exception:
            continue
    return written


class PreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, asset: Asset, out_dir: Path, all_assets: list[Asset], pinned_texture_asset_id: str | None = None, graph_related_assets: list[Asset] | None = None):
        super().__init__()
        self.asset = asset
        self.out_dir = out_dir
        self.all_assets = list(all_assets)
        # Kept for geometry-only fallback when textured preview fails. Normal model
        # preview runs texture resolution automatically via TextureResolveWorker.
        self.pinned_texture_asset_id = pinned_texture_asset_id
        self.graph_related_assets = list(graph_related_assets or [])

    def run(self) -> None:
        try:
            self.progress.emit(
                "Preparing fast geometry preview. Textures are not decoded here; use Set Textures when this is the asset you want."
            )
            self.progress.emit("Calling apicula to convert the model preview without texture or animation siblings...")
            result = convert_with_apicula(self.asset, self.out_dir, sibling_assets=(), more_textures=False)
            if result.ok:
                result.auxiliary_files = []
                self.progress.emit("Fast model conversion finished.")
                self.finished_ok.emit(self.asset.asset_id, result)
            else:
                self.failed.emit(self.asset.asset_id, result.message)
        except Exception as exc:
            self.failed.emit(self.asset.asset_id, str(exc))


class TextureResolveWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, object, str, str)
    failed = Signal(str, str)

    def __init__(
        self,
        asset: Asset,
        out_dir: Path,
        all_assets: list[Asset],
        pinned_texture_asset_id: str | None = None,
        graph_related_assets: list[Asset] | None = None,
        *,
        texture_library: TextureLibrary | None = None,
        texture_store: TextureLibraryStore | None = None,
    ):
        super().__init__()
        self.asset = asset
        self.out_dir = out_dir
        self.all_assets = list(all_assets)
        self.pinned_texture_asset_id = pinned_texture_asset_id
        self.graph_related_assets = list(graph_related_assets or [])
        self.texture_library = texture_library
        self.texture_store = texture_store

    def _resolve_textures(self, pinned: Asset | None):
        resolution = resolve_model_textures(
            self.asset,
            self.all_assets,
            texture_library=None,
            manual_texture=pinned,
            defer_library_build=True,
            progress=self.progress.emit,
        )
        if resolution.verified or (resolution.decoded_images and resolution.status != "unresolved"):
            return resolution

        library = self.texture_library
        if library is None and self.texture_store is not None:
            if self.texture_store.is_ready_for(self.all_assets):
                self.progress.emit("Set Textures: using cached ROM texture dictionary index.")
            library = self.texture_store.get_or_build(self.all_assets, progress=self.progress.emit)
        elif library is None:
            library = TextureLibrary.from_assets(self.all_assets, progress=self.progress.emit)

        return resolve_model_textures(
            self.asset,
            self.all_assets,
            texture_library=library,
            manual_texture=pinned,
            progress=self.progress.emit,
        )

    def run(self) -> None:
        try:
            if self.asset.magic != "BMD0":
                self.failed.emit(self.asset.asset_id, "Set Textures only works on BMD0/NSBMD model assets.")
                return
            self.out_dir.mkdir(parents=True, exist_ok=True)
            self.progress.emit("Set Textures: parsing the selected NSBMD/BMD0 model.")

            pinned = None
            if self.pinned_texture_asset_id:
                pinned = next((a for a in self.all_assets if a.asset_id == self.pinned_texture_asset_id and a.magic == "BTX0"), None)
                if pinned is not None:
                    self.progress.emit(f"Set Textures: manual BTX0 override is available: {pinned.virtual_path}")

            resolution = self._resolve_textures(pinned)

            report_lines = [resolution.report, ""]
            selected_texture_id = ""
            siblings = [a for a in resolution.resolved_assets if a.magic == "BTX0"]
            if resolution.status == "embedded_texture" and resolution.decoded_images:
                self.progress.emit(f"Set Textures: verified {len(resolution.decoded_images)} embedded texture image(s) inside the NSBMD. No external BTX0 will be pinned.")
            elif resolution.decoded_images and resolution.status != "unresolved":
                self.progress.emit(f"Set Textures: decoded {len(resolution.decoded_images)} texture image(s) ({resolution.status}).")
            elif resolution.verified and siblings:
                selected_texture_id = siblings[0].asset_id
                self.progress.emit(f"Set Textures: verified {len(resolution.decoded_images)} decoded texture image(s) from {len(siblings)} external texture archive(s).")
            elif resolution.status == "manual_override" and siblings:
                selected_texture_id = siblings[0].asset_id
                self.progress.emit("Set Textures: using manual texture override. Pairing is user-selected, not auto-proven.")
            else:
                self.progress.emit("Set Textures: no exact verified texture binding found. DSM will not pin a fuzzy candidate.")

            # Keep animation siblings deterministic/safe: same folder/container only.
            for item in self.graph_related_assets:
                if item.magic in {"BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"} and item.asset_id != self.asset.asset_id:
                    siblings.append(item)
            seen = {self.asset.asset_id}
            unique_siblings = []
            for item in siblings:
                if item.asset_id in seen:
                    continue
                seen.add(item.asset_id)
                unique_siblings.append(item)
            siblings = unique_siblings[:16]

            trial_dir = self.out_dir / "resolved"
            if trial_dir.exists():
                shutil.rmtree(trial_dir, ignore_errors=True)
            self.progress.emit("Set Textures: converting preview with only resolved/manual texture inputs.")
            result = convert_with_apicula(self.asset, trial_dir, sibling_assets=siblings, output_format="glb", more_textures=True)

            if result.ok and result.output_files:
                best_path = _best_preview_path(result.output_files)
                if best_path is not None:
                    result.output_files = [best_path, *[p for p in result.output_files if p != best_path]]
                aux = write_resolution_images(resolution, trial_dir / "dsm_resolved_textures")
                if aux:
                    result.auxiliary_files = aux
                    report_lines.append(f"DSM decoded texture PNGs for preview/export: {len(aux)}")
                quality = _converted_texture_quality(result.output_files[0]) if result.output_files else TextureQuality(0,0,0,0,0,0,0,0)
                report_lines.append(f"Converted preview: {result.output_files[0].name} — {quality.summary()}")
                if not quality.confident and aux:
                    report_lines.append("Note: the converted file did not embed visible texture images, so DSM uses the decoded NSBTX PNGs as preview fallback. Export the diagnostic bundle if Blender still shows gray.")
                self.finished_ok.emit(self.asset.asset_id, result, selected_texture_id, "\n".join(report_lines))
                return

            # If apicula fails, still show the resolver report and decoded texture PNGs.
            report_lines.append("apicula conversion failed or produced no mesh-bearing output.")
            report_lines.append(result.message if result else "No conversion result.")
            self.failed.emit(self.asset.asset_id, "\n".join(report_lines))
        except Exception as exc:
            self.failed.emit(self.asset.asset_id, str(exc))


def _converted_texture_quality(path: Path) -> TextureQuality:
    """Inspect a converted GLB/DAE and estimate whether a texture is actually visible.

    This is stricter than the old material-slot score. A GLB can have a texture
    visual/material object but still render gray if the image is flat, UVs are not
    usable, or the candidate texture archive is just the wrong one. The quality
    object lets DSM say “not sure” instead of pretending a candidate is correct.
    """
    try:
        import numpy as np
        import trimesh
        loaded = trimesh.load(path, force="scene")
        meshes = loaded.dump() if isinstance(loaded, trimesh.Scene) else [loaded]
        mesh_faces = 0
        texture_visuals = 0
        image_count = 0
        uv_sets = 0
        sampled_unique_colors = 0
        image_unique_colors = 0
        flat_images = 0
        score = 0

        for mesh in meshes:
            if not hasattr(mesh, "faces") or not hasattr(mesh, "vertices"):
                continue
            faces = np.asarray(getattr(mesh, "faces", []), dtype=int)
            verts = np.asarray(getattr(mesh, "vertices", []), dtype=float)
            mesh_faces += int(len(faces))
            visual = getattr(mesh, "visual", None)
            if visual is None:
                continue
            if getattr(visual, "kind", None) == "texture":
                texture_visuals += 1
                uv = getattr(visual, "uv", None)
                material = getattr(visual, "material", None)
                image = getattr(material, "image", None) if material is not None else None
                if uv is not None:
                    uv_sets += 1
                    score += 8
                if image is not None:
                    image_count += 1
                    try:
                        if hasattr(image, "convert"):
                            image = image.convert("RGBA")
                        img = np.asarray(image, dtype=np.uint8)
                        if img.ndim == 3 and img.shape[0] and img.shape[1]:
                            # Measure actual image variation on a bounded sample.
                            flat = img.reshape(-1, img.shape[-1])[:, :4]
                            if len(flat) > 2048:
                                flat = flat[:: max(1, len(flat) // 2048)]
                            unique_img = len(np.unique((flat[:, :3] // 8).astype(np.uint8), axis=0))
                            image_unique_colors = max(image_unique_colors, int(unique_img))
                            if unique_img <= 4:
                                flat_images += 1
                            score += 15 if unique_img > 4 else 3

                            # Measure what the model would visibly sample through UVs.
                            uv_arr = np.asarray(uv, dtype=float) if uv is not None else None
                            if uv_arr is not None and uv_arr.ndim == 2 and uv_arr.shape[1] >= 2 and len(faces):
                                sample_uv = None
                                if len(uv_arr) == len(verts):
                                    sample_uv = uv_arr[faces.reshape(-1), :2]
                                elif len(uv_arr) == len(faces) * 3:
                                    sample_uv = uv_arr[:, :2]
                                if sample_uv is not None and len(sample_uv):
                                    if len(sample_uv) > 4096:
                                        sample_uv = sample_uv[:: max(1, len(sample_uv) // 4096)]
                                    u = np.mod(sample_uv[:, 0], 1.0)
                                    v = np.mod(sample_uv[:, 1], 1.0)
                                    px = np.clip(np.rint(u * (img.shape[1] - 1)).astype(int), 0, img.shape[1] - 1)
                                    py = np.clip(np.rint((1.0 - v) * (img.shape[0] - 1)).astype(int), 0, img.shape[0] - 1)
                                    sampled = img[py, px, :3]
                                    unique_sampled = len(np.unique((sampled // 8).astype(np.uint8), axis=0))
                                    sampled_unique_colors = max(sampled_unique_colors, int(unique_sampled))
                                    score += 35 if unique_sampled >= 8 else (10 if unique_sampled >= 3 else 0)
                    except Exception:
                        score += 2
            elif getattr(visual, "vertex_colors", None) is not None:
                score += 4

        return TextureQuality(
            score=int(score),
            mesh_faces=int(mesh_faces),
            texture_visuals=int(texture_visuals),
            image_count=int(image_count),
            uv_sets=int(uv_sets),
            sampled_unique_colors=int(sampled_unique_colors),
            image_unique_colors=int(image_unique_colors),
            flat_images=int(flat_images),
        )
    except Exception:
        return TextureQuality(0, 0, 0, 0, 0, 0, 0, 0)


def _converted_texture_score(path: Path) -> int:
    """Compatibility wrapper used by older UI/status code."""
    return _converted_texture_quality(path).score

def _converted_mesh_score(path: Path) -> int:
    """Return a rough geometry score so camera-only GLBs do not get previewed."""
    try:
        import trimesh
        loaded = trimesh.load(path, force="scene")
        meshes = loaded.dump() if isinstance(loaded, trimesh.Scene) else [loaded]
        return sum(int(len(getattr(mesh, "faces", []))) for mesh in meshes if hasattr(mesh, "faces"))
    except Exception:
        return 0


def _best_preview_path(paths: list[Path]) -> Path | None:
    """Pick the converted file that is most likely to be visible in DSM.

    apicula can emit helper/camera GLBs alongside real geometry. Older DSM builds
    previewed the first file, which could show errors like camera3.glb having no
    mesh. Prefer files with faces, then files with texture data, then the first
    output as a last resort.
    """
    if not paths:
        return None
    ranked = sorted(
        paths,
        key=lambda path: (_converted_mesh_score(path), _converted_texture_score(path), -len(path.name)),
        reverse=True,
    )
    return ranked[0]


class ImagePreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, str, str)
    failed = Signal(str, str)

    def __init__(self, asset: Asset, out_path: Path, label: str, related_assets: list[Asset] | None = None):
        super().__init__()
        self.asset = asset
        self.out_path = out_path
        self.label = label
        self.related_assets = related_assets or []

    def run(self) -> None:
        try:
            self.progress.emit(f"Decoding preview images from {self.asset.virtual_path}...")
            if self.asset.magic == "BTX0":
                images = decode_btx_images(self.asset.data, max_images=96, mode="all-palettes")
            elif self.related_assets:
                self.progress.emit(f"Composing preview with {len(self.related_assets)} related asset(s)...")
                images = decode_nitro2d_related_preview(self.asset, self.related_assets)
            else:
                images = decode_nitro2d_preview(self.asset.data, self.asset.magic)
            if not images:
                self.failed.emit(self.asset.asset_id, "No readable preview images were decoded from this asset yet.")
                return
            sheet = make_contact_sheet(images, columns=4) if len(images) > 1 else images[0].to_pil()
            if sheet is None:
                self.failed.emit(self.asset.asset_id, "No preview sheet could be created.")
                return
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(self.out_path)
            names = ", ".join(img.name for img in images[:8])
            if len(images) > 8:
                names += ", ..."
            caption = f"{self.label}: {len(images)} decoded image(s). {names}\n{self.asset.virtual_path}"
            self.finished_ok.emit(self.asset.asset_id, str(self.out_path), caption)
        except Exception as exc:
            self.failed.emit(self.asset.asset_id, str(exc))


class TextureWorker(QThread):
    finished_ok = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, asset: Asset, out_dir: Path):
        super().__init__()
        self.asset = asset
        self.out_dir = out_dir

    def run(self) -> None:
        try:
            result = convert_texture_with_apicula(self.asset, self.out_dir)
            if result.ok:
                self.finished_ok.emit(self.asset.asset_id, result)
            else:
                self.failed.emit(self.asset.asset_id, result.message)
        except Exception as exc:
            self.failed.emit(self.asset.asset_id, str(exc))


def _qcolor_rgbf(hex_color: str) -> tuple[float, float, float, float]:
    color = QColor(hex_color)
    return color.redF(), color.greenF(), color.blueF(), 1.0


class PreviewCanvas(QFrame):
    """Painted viewport background for empty/image preview modes."""

    def __init__(self, preview: "PreviewWidget"):
        super().__init__()
        self._preview = preview
        self.setFrameShape(QFrame.NoFrame)
        self.setAutoFillBackground(False)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        name = self._preview._background_name
        if name == "Checkered":
            painter.fillRect(self.rect(), self._preview._checkered_brush)
        elif name == "Black":
            painter.fillRect(self.rect(), QColor("#141414"))
        else:
            painter.fillRect(self.rect(), QColor("#ffffff"))
        painter.end()


try:
    from OpenGL import GL as _GL
    import pyqtgraph.opengl as gl

    class PreviewGLView(gl.GLViewWidget):
        """GL viewport that paints the checker/solid background inside OpenGL."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._preview_background_name = "Checkered"

        def set_preview_background_name(self, name: str) -> None:
            self._preview_background_name = name if name in {"White", "Checkered", "Black"} else "Checkered"
            self.update()

        def paint(self, *, region, viewport, useItemNames=False):
            name = self._preview_background_name
            if name == "Checkered":
                self._paint_checker(viewport)
            else:
                rgba = {
                    "White": (1.0, 1.0, 1.0, 1.0),
                    "Black": (0.08, 0.08, 0.08, 1.0),
                }.get(name, (0.28, 0.28, 0.28, 1.0))
                _GL.glClearColor(*rgba)
                _GL.glClear(_GL.GL_COLOR_BUFFER_BIT | _GL.GL_DEPTH_BUFFER_BIT)
            self.setProjection(region, viewport)
            self.setModelview()
            _GL.glClear(_GL.GL_DEPTH_BUFFER_BIT)
            self.drawItemTree(useItemNames=useItemNames)

        def _paint_checker(self, viewport) -> None:
            _x, _y, width, height = viewport
            tile = 14
            light = _qcolor_rgbf("#4a4a4a")
            dark = _qcolor_rgbf("#353535")
            _GL.glDisable(_GL.GL_DEPTH_TEST)
            _GL.glDisable(_GL.GL_LIGHTING)
            _GL.glMatrixMode(_GL.GL_PROJECTION)
            _GL.glPushMatrix()
            _GL.glLoadIdentity()
            _GL.glOrtho(0, width, height, 0, -1, 1)
            _GL.glMatrixMode(_GL.GL_MODELVIEW)
            _GL.glPushMatrix()
            _GL.glLoadIdentity()
            for y in range(0, height + tile, tile):
                for x in range(0, width + tile, tile):
                    if ((x // tile) + (y // tile)) % 2 == 0:
                        _GL.glColor4f(*dark)
                    else:
                        _GL.glColor4f(*light)
                    x2 = min(x + tile, width)
                    y2 = min(y + tile, height)
                    _GL.glBegin(_GL.GL_QUADS)
                    _GL.glVertex2f(x, y)
                    _GL.glVertex2f(x2, y)
                    _GL.glVertex2f(x2, y2)
                    _GL.glVertex2f(x, y2)
                    _GL.glEnd()
            _GL.glPopMatrix()
            _GL.glMatrixMode(_GL.GL_PROJECTION)
            _GL.glPopMatrix()
            _GL.glMatrixMode(_GL.GL_MODELVIEW)
            _GL.glEnable(_GL.GL_DEPTH_TEST)

except Exception:
    PreviewGLView = None  # type: ignore[misc, assignment]


class PreviewWidget(QWidget):
    """OpenGL / image preview canvas with a compact top chrome row."""

    def __init__(self):
        super().__init__()
        self._available = False
        self._view = None
        self._mesh_items = []
        self._image_label = None
        self._last_path: Path | None = None
        self._fallback_texture_paths: list[Path] = []
        self._fallback_texture_image = None
        self._fallback_texture_images: dict[str, object] = {}
        self._fallback_texture_path_order: list[Path] = []
        self._use_textures = True
        self._wireframe = False
        self._background_name = "Checkered"
        self._image_source: QPixmap | None = None
        self._image_zoom = 1.0
        self._model_distance = 90.0

        # Hidden toggles kept for Preview menu sync.
        self.textures_toggle = QPushButton()
        self.textures_toggle.setCheckable(True)
        self.textures_toggle.setChecked(True)
        self.textures_toggle.hide()
        self.wireframe_toggle = QPushButton()
        self.wireframe_toggle.setCheckable(True)
        self.wireframe_toggle.hide()
        self.textures_toggle.toggled.connect(self.set_use_textures)
        self.wireframe_toggle.toggled.connect(self.set_wireframe)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        chrome = QWidget()
        chrome_layout = QHBoxLayout(chrome)
        chrome_layout.setContentsMargins(6, 4, 6, 4)
        chrome_layout.setSpacing(6)
        chrome_layout.addStretch()

        self._action_host = QWidget()
        self.action_layout = QHBoxLayout(self._action_host)
        self.action_layout.setContentsMargins(0, 0, 0, 0)
        self.action_layout.setSpacing(6)
        chrome_layout.addWidget(self._action_host)

        self._bg_combo = QComboBox()
        self._bg_combo.addItems(["Checkered", "White", "Black"])
        self._bg_combo.setCurrentIndex(0)
        self._bg_combo.setToolTip("Preview background")
        self._bg_combo.setFixedWidth(96)
        self._bg_combo.currentTextChanged.connect(self._on_background_changed)
        chrome_layout.addWidget(self._bg_combo)

        self._checkered_brush = self._make_checkered_brush()
        self._canvas = PreviewCanvas(self)
        self._canvas.setMinimumHeight(280)
        self._canvas.setMouseTracking(True)
        canvas_layout = QGridLayout(self._canvas)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.setSpacing(0)

        self._banner = QLabel()
        self._banner.setWordWrap(True)
        self._banner.setStyleSheet(VIEWPORT_BANNER_STYLE)
        self._banner.hide()
        self._banner.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        try:
            import numpy as np  # noqa: F401
            import trimesh  # noqa: F401
            import pyqtgraph.opengl as gl  # noqa: F401

            view_cls = PreviewGLView if PreviewGLView is not None else gl.GLViewWidget
            self._view = view_cls()
            self._view.setCameraPosition(distance=self._model_distance)
            if hasattr(self._view, "set_preview_background_name"):
                self._view.set_preview_background_name(self._background_name)
            else:
                self._view.opts["bgcolor"] = (0.28, 0.28, 0.28, 1.0)
            self._axis = gl.GLAxisItem()
            self._view.addItem(self._axis)
            canvas_layout.addWidget(self._view, 0, 0)
            self._available = True
        except Exception:
            placeholder = QLabel("3D preview unavailable.\nInstall requirements.txt to enable model preview.")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setWordWrap(True)
            placeholder.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            canvas_layout.addWidget(placeholder, 0, 0)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignCenter)
        self._image_label.setWordWrap(True)
        self._image_label.setStyleSheet("background: transparent;")
        canvas_layout.addWidget(self._image_label, 0, 0)
        self._image_label.hide()
        self._image_label.installEventFilter(self)

        self._message_label = QLabel()
        self._message_label.setAlignment(Qt.AlignCenter)
        self._message_label.setWordWrap(True)
        self._message_label.setStyleSheet("background: transparent; color: #b8b8b8; padding: 16px;")
        self._message_label.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        canvas_layout.addWidget(self._message_label, 0, 0)
        self._message_label.hide()

        canvas_layout.addWidget(self._banner, 0, 0, alignment=Qt.AlignTop)
        root.addWidget(chrome)
        root.addWidget(self._canvas, stretch=1)
        self._apply_background("Checkered")
        self.setFocusPolicy(Qt.StrongFocus)
        self._canvas.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(True)
        self.installEventFilter(self)
        self._canvas.installEventFilter(self)
        if self._view is not None:
            self._view.installEventFilter(self)

    def _make_checkered_brush(self) -> QBrush:
        tile = 14
        pm = QPixmap(tile * 2, tile * 2)
        pm.fill(QColor(CHECKER_LIGHT))
        painter = QPainter(pm)
        painter.fillRect(0, 0, tile, tile, QColor(CHECKER_DARK))
        painter.fillRect(tile, tile, tile, tile, QColor(CHECKER_DARK))
        painter.end()
        return QBrush(pm)

    def set_banner(self, text: str = "") -> None:
        text = str(text or "").strip()
        if text:
            self._banner.setText(text)
            self._banner.show()
            self._banner.raise_()
        else:
            self._banner.clear()
            self._banner.hide()

    def _on_background_changed(self, name: str) -> None:
        self._apply_background(name)

    def _refresh_background_tiles(self) -> None:
        self._canvas.update()
        if self._view is not None:
            if hasattr(self._view, "set_preview_background_name"):
                self._view.set_preview_background_name(self._background_name)
            else:
                rgba = GL_BG_COLORS.get(self._background_name, GL_BG_COLORS["Checkered"])
                try:
                    self._view.opts["bgcolor"] = rgba
                except Exception:
                    pass
            try:
                self._view.update()
            except Exception:
                pass

    def _apply_background(self, name: str) -> None:
        allowed = {"White", "Checkered", "Black"}
        self._background_name = name if name in allowed else "Checkered"
        self._refresh_background_tiles()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_background_tiles()
        if self._image_source is not None and not self._image_source.isNull():
            self._refresh_image_display()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            super().wheelEvent(event)
            return
        if self._image_source is not None and not self._image_source.isNull() and self._image_label.isVisible():
            factor = 1.12 if delta > 0 else 1 / 1.12
            self._image_zoom = max(0.08, min(12.0, self._image_zoom * factor))
            self._refresh_image_display()
            event.accept()
            return
        if self._view is not None and self._view.isVisible() and self._mesh_items:
            factor = 0.9 if delta > 0 else 1.1
            self._model_distance = max(8.0, min(600.0, self._model_distance * factor))
            try:
                self._view.setCameraPosition(distance=self._model_distance)
            except Exception:
                pass
            event.accept()
            return
        super().wheelEvent(event)

    def _refresh_image_display(self) -> None:
        if self._image_source is None or self._image_source.isNull():
            return
        src_w = max(1, self._image_source.width())
        src_h = max(1, self._image_source.height())
        canvas_w = max(1, self._canvas.width() - 24)
        canvas_h = max(1, self._canvas.height() - 24)
        fit = min(canvas_w / src_w, canvas_h / src_h, 1.0)
        scale = fit * self._image_zoom
        if scale >= 1.0:
            pixel_scale = max(1, int(round(scale)))
            target_w = src_w * pixel_scale
            target_h = src_h * pixel_scale
        else:
            target_w = max(1, int(src_w * scale))
            target_h = max(1, int(src_h * scale))
        scaled = self._image_source.scaled(
            target_w,
            target_h,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        # Keep DS sprite pixels crisp on HiDPI displays.
        scaled.setDevicePixelRatio(1.0)
        self._image_label.setPixmap(scaled)

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Wheel and watched in {self, self._canvas, self._view, self._image_label}:
            self.wheelEvent(event)
            return True
        return super().eventFilter(watched, event)

    def _clear_meshes(self) -> None:
        if self._view is not None:
            for item in self._mesh_items:
                self._view.removeItem(item)
            self._mesh_items.clear()

    def clear(self) -> None:
        self._clear_meshes()
        self._image_source = None
        self._image_zoom = 1.0
        self._image_label.clear()
        self._image_label.hide()
        self._message_label.clear()
        self._message_label.hide()
        if self._view is not None:
            self._view.hide()

    def set_use_textures(self, enabled: bool) -> None:
        self._use_textures = enabled
        if self._last_path is not None:
            self.load_glb(self._last_path, fallback_textures=self._fallback_texture_paths)

    def set_wireframe(self, enabled: bool) -> None:
        self._wireframe = enabled
        if self._last_path is not None:
            self.load_glb(self._last_path, fallback_textures=self._fallback_texture_paths)

    def show_message(self, text: str) -> None:
        self.clear()
        self.set_banner("")
        text = str(text or "").strip()
        if text:
            self._message_label.setText(text)
            self._message_label.show()
        self._refresh_background_tiles()

    def show_image_path(self, path: Path, caption: str = "") -> None:
        self.clear()
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.set_banner(f"Could not load image preview: {path.name}")
            return
        self.set_banner(caption if caption and "warning" in caption.lower() else "")
        self._message_label.hide()
        self._image_source = pixmap
        self._image_zoom = 1.0
        if self._view is not None:
            self._view.hide()
        self._refresh_image_display()
        self._image_label.show()
        self._refresh_background_tiles()

    def load_glb(self, path: Path, *, fallback_textures: list[Path] | None = None) -> None:
        self._last_path = path
        if fallback_textures is not None:
            self._fallback_texture_paths = list(fallback_textures)
            self._fallback_texture_image = None
            self._fallback_texture_images = {}
            self._fallback_texture_path_order = []
        if not self._available or self._view is None:
            self.set_banner(f"Converted file ready: {path.name}")
            return

        try:
            import numpy as np
            import trimesh
            import pyqtgraph.opengl as gl
        except Exception as exc:
            self.set_banner(f"Preview dependencies unavailable: {exc}")
            return

        try:
            self._message_label.hide()
            self._image_label.hide()
            self._view.show()
            self._refresh_background_tiles()
            loaded = trimesh.load(path, force="scene")
            meshes = self._extract_meshes(loaded, trimesh)
            if not meshes:
                self._view.hide()
                self.set_banner(f"Converted file loaded, but no mesh faces were found: {path.name}")
                self._refresh_background_tiles()
                return

            # Rotate first, then center/scale the whole scene as one object.
            rotated_vertices = []
            for mesh in meshes:
                vertices = np.asarray(mesh.vertices, dtype=float)
                vertices = self._apply_preview_orientation(vertices, np)
                rotated_vertices.append(vertices)

            all_vertices = np.vstack(rotated_vertices)
            center = all_vertices.mean(axis=0)
            extent = float(np.max(np.ptp(all_vertices - center, axis=0))) or 1.0
            scale = 40.0 / extent

            self._clear_meshes()
            textured_meshes = 0
            colored_meshes = 0
            for mesh_index, (mesh, vertices) in enumerate(zip(meshes, rotated_vertices)):
                if len(mesh.faces) == 0:
                    continue
                vertices = (vertices - center) * scale
                faces = np.asarray(mesh.faces, dtype=int)

                # pyqtgraph's GLMeshItem cannot render UV-mapped textures. Older
                # DSM builds tried to fall back to the material color when the
                # imported UV array did not line up one-to-one with vertices.
                # Many GLB/DAE imports store UVs per face corner, so that fallback
                # made genuinely textured models appear gray. Bake the texture
                # into per-corner vertex colors for preview only. Exports remain
                # untouched.
                texture_display = self._texture_baked_display_geometry(mesh, vertices, faces, np, mesh_index=mesh_index) if self._use_textures else None
                if texture_display is not None:
                    display_vertices, display_faces, colors = texture_display
                    face_colors = None
                    textured_meshes += 1
                else:
                    display_vertices = vertices
                    display_faces = faces
                    colors = self._mesh_vertex_colors(mesh, np, mesh_index=mesh_index) if self._use_textures else None
                    face_colors = None if colors is not None else self._mesh_face_colors(mesh, np, mesh_index=mesh_index)

                kwargs = dict(
                    vertexes=display_vertices,
                    faces=display_faces,
                    drawFaces=True,
                    drawEdges=self._wireframe or (colors is None and face_colors is None),
                    smooth=False,
                    shader="shaded",
                )
                if colors is not None:
                    kwargs["vertexColors"] = colors
                    if texture_display is None:
                        colored_meshes += 1
                elif face_colors is not None:
                    kwargs["faceColors"] = face_colors
                    colored_meshes += 1

                item = gl.GLMeshItem(**kwargs)
                self._view.addItem(item)
                self._mesh_items.append(item)

            self._view.setCameraPosition(distance=self._model_distance)
            self._view.show()
            self._refresh_background_tiles()
            warning = ""
            if self._use_textures and not textured_meshes and not colored_meshes:
                if self._fallback_texture_paths:
                    warning = (
                        "Decoded texture PNGs are available, but this converted mesh "
                        "did not expose bindable UV texture data for preview."
                    )
                else:
                    warning = "No renderable texture data in this converted file."
            elif not self._use_textures:
                warning = "Textures hidden — enable Preview → Textures to show decoded colors."
            self.set_banner(warning)
        except Exception as exc:
            self.set_banner(f"Could not preview {path.name}: {exc}")

    def _extract_meshes(self, loaded, trimesh):
        if isinstance(loaded, trimesh.Scene):
            raw = loaded.dump()
            return [m for m in raw if isinstance(m, trimesh.Trimesh) and len(m.vertices) and len(m.faces)]
        if isinstance(loaded, trimesh.Trimesh) and len(loaded.vertices) and len(loaded.faces):
            return [loaded]
        return []

    def _apply_preview_orientation(self, vertices, np):
        # Pokémon B2W2/apicula preview fix: the converted model's +Y axis is the
        # vertical axis we want to show as +Z in DSM's preview. This is preview-only;
        # exported GLB/DAE files are left exactly as apicula writes them.
        x = vertices[:, 0].copy()
        y = vertices[:, 1].copy()
        z = vertices[:, 2].copy()
        return np.column_stack((x, z, y))

    def _texture_baked_display_geometry(self, mesh, vertices, faces, np, *, mesh_index: int = 0):
        """Return preview-only geometry with texture sampled into vertex colors.

        GLMeshItem has no UV texture stage. To make converted GLB/DAE textures
        visible inside DSM, duplicate each triangle corner and color it by the
        texel at that corner's UV. This preserves DS/glTF wrap behavior well
        enough for browsing while keeping the exported files exactly as apicula
        wrote them.
        """
        visual = getattr(mesh, "visual", None)
        if visual is None or getattr(visual, "kind", None) != "texture":
            return None
        image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index)
        uv = getattr(visual, "uv", None)
        if image is None or uv is None:
            return None
        uv_arr = np.asarray(uv, dtype=float)
        if uv_arr.ndim != 2 or uv_arr.shape[1] < 2 or len(faces) == 0:
            return None

        try:
            source_faces = np.asarray(mesh.faces, dtype=int)
            if len(uv_arr) == len(mesh.vertices):
                flat_uv = uv_arr[source_faces.reshape(-1), :2]
            elif len(uv_arr) == len(source_faces) * 3:
                flat_uv = uv_arr[:, :2]
            else:
                return None

            flat_vertices = vertices[faces.reshape(-1)]
            colors = self._sample_image_at_uv(flat_uv, image, np)
            if colors is None or len(colors) != len(flat_vertices):
                return None
            display_faces = np.arange(len(flat_vertices), dtype=int).reshape(-1, 3)
            return flat_vertices, display_faces, colors
        except Exception:
            return None

    def _mesh_vertex_colors(self, mesh, np, *, mesh_index: int = 0):
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None

        # Best case: apicula/trimesh gave us UVs plus an image. Sample the image
        # to vertex colors because GLMeshItem does not support UV texture maps.
        if getattr(visual, "kind", None) == "texture":
            uv = getattr(visual, "uv", None)
            image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index)
            if uv is not None and image is not None and len(uv) == len(mesh.vertices):
                colors = self._sample_image_at_uv(uv, image, np)
                if colors is not None:
                    return colors

            # Fallback: use the material base color only when there is no texture
            # image to sample. If an image exists but the UV layout is per-face,
            # _texture_baked_display_geometry should handle it; returning a gray
            # material here would hide the real texture.
            if image is None:
                material = getattr(visual, "material", None)
                material_color = self._material_color(material, np)
                if material_color is not None:
                    return np.tile(material_color, (len(mesh.vertices), 1))

        # Vertex-color assets can be rendered directly.
        vertex_colors = getattr(visual, "vertex_colors", None)
        if vertex_colors is not None and len(vertex_colors) == len(mesh.vertices):
            colors = np.asarray(vertex_colors, dtype=float)
            if colors.max(initial=1.0) > 1.0:
                colors = colors / 255.0
            if colors.shape[1] == 3:
                colors = np.column_stack([colors, np.ones(len(colors))])
            return colors[:, :4]
        return None

    def _mesh_face_colors(self, mesh, np, *, mesh_index: int = 0):
        visual = getattr(mesh, "visual", None)
        if visual is None:
            return None

        # More robust texture preview: some glTF/DAE imports keep UVs per face
        # corner instead of per vertex. GLMeshItem cannot render UV textures, but
        # it can render face colors, so average each triangle's sampled texels.
        if getattr(visual, "kind", None) == "texture":
            uv = getattr(visual, "uv", None)
            image = self._visual_image(visual, mesh=mesh, mesh_index=mesh_index)
            if uv is not None and image is not None:
                uv_arr = np.asarray(uv, dtype=float)
                try:
                    if len(uv_arr) == len(mesh.vertices):
                        face_uv = uv_arr[np.asarray(mesh.faces, dtype=int)].reshape(-1, 2)
                        sampled = self._sample_image_at_uv(face_uv, image, np)
                        if sampled is not None:
                            return sampled.reshape(len(mesh.faces), 3, 4).mean(axis=1)
                    if len(uv_arr) == len(mesh.faces) * 3:
                        sampled = self._sample_image_at_uv(uv_arr, image, np)
                        if sampled is not None:
                            return sampled.reshape(len(mesh.faces), 3, 4).mean(axis=1)
                except Exception:
                    pass

        face_colors = getattr(visual, "face_colors", None)
        if face_colors is None or len(face_colors) != len(mesh.faces):
            return None
        colors = np.asarray(face_colors, dtype=float)
        if colors.max(initial=1.0) > 1.0:
            colors = colors / 255.0
        if colors.shape[1] == 3:
            colors = np.column_stack([colors, np.ones(len(colors))])
        return colors[:, :4]

    def _visual_image(self, visual, *, mesh=None, mesh_index: int = 0):
        material = getattr(visual, "material", None)
        image = getattr(material, "image", None) if material is not None else None
        if image is not None:
            return image
        # Some apicula outputs keep UV/material slots but no embedded GLB image
        # even when DSM has decoded the Nitro TEX0/BTX0 PNGs correctly. Do not
        # reuse one global fallback image for every mesh part: many Pokémon map
        # props have multiple materials/textures. Prefer a fallback whose file
        # name matches the material/mesh name, then fall back to a stable
        # per-mesh round-robin so multi-texture models visibly use more than the
        # first decoded image.
        self._ensure_fallback_texture_cache()
        if not self._fallback_texture_images:
            return None

        keys = self._fallback_match_keys(visual, mesh)
        for key in keys:
            match = self._find_fallback_image_by_key(key)
            if match is not None:
                return match

        if self._fallback_texture_path_order:
            path = self._fallback_texture_path_order[mesh_index % len(self._fallback_texture_path_order)]
            return self._fallback_texture_images.get(str(path))
        return None

    def _ensure_fallback_texture_cache(self) -> None:
        if self._fallback_texture_images or not self._fallback_texture_paths:
            return
        try:
            from PIL import Image as PILImage
        except Exception:
            return
        ordered: list[Path] = []
        for path in self._fallback_texture_paths:
            try:
                img = PILImage.open(path).convert("RGBA")
            except Exception:
                continue
            ordered.append(path)
            self._fallback_texture_images[str(path)] = img
            stem = path.stem.casefold()
            self._fallback_texture_images[stem] = img
            # Decoded variants often include palette names or numeric suffixes;
            # index useful prefixes too, e.g. gym02_001tga__pal and
            # resolved_texture_00_gym02_001tga.
            for token in re.split(r"[^A-Za-z0-9_]+", path.stem):
                token = token.strip("_").casefold()
                if len(token) >= 3 and token not in self._fallback_texture_images:
                    self._fallback_texture_images[token] = img
            for part in path.stem.split("__"):
                part = part.strip("_").casefold()
                if len(part) >= 3 and part not in self._fallback_texture_images:
                    self._fallback_texture_images[part] = img
        self._fallback_texture_path_order = ordered
        if ordered:
            self._fallback_texture_image = self._fallback_texture_images.get(str(ordered[0]))

    def _fallback_match_keys(self, visual, mesh=None) -> list[str]:
        keys: list[str] = []
        material = getattr(visual, "material", None)
        for obj in (material, visual, mesh):
            if obj is None:
                continue
            for attr in ("name", "image_name", "file_name"):
                value = getattr(obj, attr, None)
                if isinstance(value, str) and value:
                    keys.append(value)
            meta = getattr(obj, "metadata", None)
            if isinstance(meta, dict):
                for value in meta.values():
                    if isinstance(value, str) and value:
                        keys.append(value)
        out: list[str] = []
        seen: set[str] = set()
        for key in keys:
            base = Path(str(key)).stem
            variants = [base, base.replace(".tga", ""), base.replace("_pl", "")]
            for item in variants:
                norm = item.strip("_").casefold()
                if len(norm) >= 3 and norm not in seen:
                    seen.add(norm)
                    out.append(norm)
        return out

    def _find_fallback_image_by_key(self, key: str):
        key = key.casefold().strip("_")
        if not key:
            return None
        direct = self._fallback_texture_images.get(key)
        if direct is not None:
            return direct
        for stored_key, image in self._fallback_texture_images.items():
            if len(stored_key) < 3:
                continue
            if key in stored_key or stored_key in key:
                return image
        return None

    def _sample_image_at_uv(self, uv, image, np):
        try:
            if hasattr(image, "convert"):
                image = image.convert("RGBA")
            img = np.asarray(image, dtype=np.uint8)
            if img.ndim != 3 or img.shape[0] <= 0 or img.shape[1] <= 0:
                return None
            if img.shape[2] == 3:
                alpha = np.full((img.shape[0], img.shape[1], 1), 255, dtype=np.uint8)
                img = np.concatenate([img, alpha], axis=2)
            uv_arr = np.asarray(uv, dtype=float)
            if uv_arr.ndim != 2 or uv_arr.shape[1] < 2:
                return None
            u = np.mod(uv_arr[:, 0], 1.0)
            v = np.mod(uv_arr[:, 1], 1.0)
            px = np.clip(np.rint(u * (img.shape[1] - 1)).astype(int), 0, img.shape[1] - 1)
            py = np.clip(np.rint((1.0 - v) * (img.shape[0] - 1)).astype(int), 0, img.shape[0] - 1)
            return img[py, px, :4].astype(float) / 255.0
        except Exception:
            return None

    def _material_color(self, material, np):
        if material is None:
            return None
        for attr in ("baseColorFactor", "diffuse"):
            value = getattr(material, attr, None)
            if value is None:
                continue
            arr = np.asarray(value, dtype=float).reshape(-1)
            if arr.size >= 3:
                if arr.max(initial=1.0) > 1.0:
                    arr = arr / 255.0
                if arr.size == 3:
                    arr = np.concatenate([arr, [1.0]])
                return arr[:4]
        return None


BROWSER_COLUMNS = ["Name", "File", "Type", "Path"]
FLAT_COLUMNS = ["#", "Name", "File", "Type", "Path"]
TYPE_LABELS = {
    "BMD0": "Model",
    "BTX0": "Texture",
    "BCA0": "Skel anim",
    "BTA0": "Tex SRT anim",
    "BTP0": "Tex pattern",
    "BMA0": "Mat anim",
    "BVA0": "Vis anim",
    "BPC0": "Color anim",
    "RGCN": "Tiles",
    "RLCN": "Palette",
    "RCSN": "Tilemap",
    "RECN": "Sprite cells",
    "RNAN": "Sprite anim",
    "NFTR": "Font",
    "PNG": "PNG",
    "SDAT": "Sound archive",
    "SSEQ": "Sequence",
    "SSAR": "SFX archive",
    "SBNK": "Instruments",
    "SWAR": "Wave archive",
    "SWAV": "Sample",
    "STRM": "Stream",
}
TYPE_FILTER_ORDER = (
    "BMD0",
    "BTX0",
    "RGCN",
    "RLCN",
    "RCSN",
    "RECN",
    "RNAN",
    "NFTR",
    "PNG",
    "BCA0",
    "BTA0",
    "BTP0",
    "BMA0",
    "BVA0",
    "BPC0",
    "SDAT",
    "SSEQ",
    "SSAR",
    "SBNK",
    "SWAR",
    "SWAV",
    "STRM",
)
TYPE_FILTER_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Models & textures", ("BMD0", "BTX0")),
    ("2D graphics", ("RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR", "PNG")),
    ("Model animation", ("BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0")),
    ("Audio", ("SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM")),
)
DEFAULT_TYPE_FILTER_ON = frozenset({"BMD0", "BTX0"})
FILTER_CHIP_STYLE = (
    "QPushButton { padding: 4px 10px; border: 1px solid #666; border-radius: 6px; "
    "background: #ececec; color: #111; }"
    "QPushButton:checked { background: #2563eb; border-color: #1d4ed8; color: #fff; font-weight: 600; }"
)
DROPDOWN_BUTTON_STYLE = (
    "QPushButton { padding: 4px 12px; border: 1px solid #585858; border-radius: 4px; "
    "background: #454545; color: #ececec; text-align: left; min-height: 22px; }"
    "QPushButton::menu-indicator { subcontrol-origin: padding; subcontrol-position: center right; "
    "padding-right: 8px; }"
    "QPushButton:hover { background: #525252; }"
)
CHROME_BUTTON_STYLE = (
    "QPushButton, QToolButton {"
    " background: #454545; color: #ececec; border: 1px solid #585858;"
    " border-radius: 4px; padding: 4px 10px; min-height: 22px; min-width: 28px;"
    "}"
    "QPushButton:hover, QToolButton:hover { background: #525252; }"
    "QPushButton:disabled, QToolButton:disabled { background: #383838; color: #888888; }"
)
CHECKER_LIGHT = "#4a4a4a"
CHECKER_DARK = "#353535"
GL_BG_COLORS = {
    "White": (1.0, 1.0, 1.0, 1.0),
    "Checkered": (0.28, 0.28, 0.28, 1.0),
    "Black": (0.08, 0.08, 0.08, 1.0),
}
VIEWPORT_BANNER_STYLE = (
    "background: rgba(30, 30, 30, 220); color: #ececec;"
    "padding: 5px 10px; font-size: 11px; border-bottom: 1px solid #585858;"
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DSM — DS Asset Studio")
        self.resize(1280, 760)

        self.rom_path: str | None = None
        self.assets: list[Asset] = []
        self.visible_assets: list[Asset] = []
        self.worker: ScanWorker | None = None
        self.relationship_worker: RelationshipWorker | None = None
        self.preview_worker: PreviewWorker | None = None
        self.image_preview_worker: ImagePreviewWorker | None = None
        self.texture_worker: TextureWorker | None = None
        self.texture_resolve_worker: TextureResolveWorker | None = None
        self.texture_warmup_worker: TextureLibraryWarmupWorker | None = None
        self.session_save_worker: SessionSaveWorker | None = None
        self.session_load_worker: SessionLoadWorker | None = None
        self.preview_temp = Path(tempfile.mkdtemp(prefix="dsm_preview_"))
        self.profile_text = ""
        self._queued_preview_asset_id: str | None = None
        self._last_previewed_asset_id: str | None = None
        self._pinned_texture_asset_id: str | None = None
        self._texture_library_store = TextureLibraryStore()
        self._texture_resolution_cache: dict[str, CachedTextureResolution] = {}
        self._texture_preview_switch_to_details = False
        self._name_cache: dict[str, set[str]] = {}
        self._display_name_cache: dict[str, str] = {}
        self.current_mapping = None
        self.asset_graph = AssetGraph()
        self.assets_by_id: dict[str, Asset] = {}
        self._selected_asset_id: str | None = None
        self.page_size = 500
        self.browser_page = 0
        self._type_filter_checkboxes: dict[str, QCheckBox] = {}
        self._type_filter_preferences: dict[str, bool] = {}
        self._tree_group_rows: dict[tuple[str, ...], list[int]] = {}
        self._tree_loaded_groups: set[tuple[str, ...]] = set()
        self._relationship_source_ids: set[str] = set()
        self._relationship_target_ids: set[str] = set()
        self.session_path: str | None = None
        self._relationship_request_asset_id: str | None = None
        self._last_texture_resolve_report: dict[str, str] = {}
        self._preview_status_by_asset_id: dict[str, str] = {}
        self._preview_fallback_count_by_asset_id: dict[str, int] = {}
        self._raw_tree_group_rows: dict[tuple[str, ...], list[int]] = {}
        self._raw_tree_loaded_groups: set[tuple[str, ...]] = set()

        self._build_ui()
        self._update_status("Open a local .nds ROM to start. Use ./dsm run next time to launch this app.")

    def _build_ui(self) -> None:
        menubar = self.menuBar()

        file_menu = menubar.addMenu("&File")
        open_rom_action = QAction("Open ROM…", self)
        open_rom_action.setShortcut("Ctrl+O")
        open_rom_action.triggered.connect(self.open_rom)
        file_menu.addAction(open_rom_action)

        open_session_action = QAction("Open Session…", self)
        open_session_action.triggered.connect(self.open_session)
        file_menu.addAction(open_session_action)

        save_session_action = QAction("Save Session…", self)
        save_session_action.setShortcut("Ctrl+S")
        save_session_action.triggered.connect(self.save_session)
        file_menu.addAction(save_session_action)

        file_menu.addSeparator()
        export_action = QAction("Export Selected…", self)
        export_action.setShortcut("Ctrl+E")
        export_action.triggered.connect(self.export_selected_smart)
        file_menu.addAction(export_action)

        view_menu = menubar.addMenu("&View")
        self.auto_preview_action = QAction("Auto Preview", self)
        self.auto_preview_action.setCheckable(True)
        self.auto_preview_action.setChecked(True)
        self.auto_preview_action.setToolTip("Preview the selected row automatically.")
        self.auto_preview_action.toggled.connect(lambda enabled: self._schedule_auto_preview() if enabled else None)
        view_menu.addAction(self.auto_preview_action)

        self.deep_scan_action = QAction("Deep Scan (next ROM open)", self)
        self.deep_scan_action.setCheckable(True)
        self.deep_scan_action.setChecked(False)
        self.deep_scan_action.setToolTip("Slower fallback scan that carves Nitro files out of unknown containers.")
        view_menu.addAction(self.deep_scan_action)

        show_terminal_action = QAction("Show Terminal", self)
        show_terminal_action.triggered.connect(lambda: self.info_tabs.setCurrentWidget(self.log_box))
        view_menu.addAction(show_terminal_action)

        advanced_menu = menubar.addMenu("&Advanced")
        build_rel_action = QAction("Build Relationships", self)
        build_rel_action.triggered.connect(self.build_relationships_for_selected)
        advanced_menu.addAction(build_rel_action)
        pin_texture_action = QAction("Pin Selected BTX0", self)
        pin_texture_action.triggered.connect(self.pin_selected_texture)
        advanced_menu.addAction(pin_texture_action)
        clear_texture_action = QAction("Clear Texture Pin", self)
        clear_texture_action.triggered.connect(self.clear_pinned_texture)
        advanced_menu.addAction(clear_texture_action)

        self.export_action = export_action
        self.open_rom_action = open_rom_action

        toolbar = QToolBar("Main")
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        open_rom_toolbar = QAction("Open ROM", self)
        open_rom_toolbar.triggered.connect(self.open_rom)
        toolbar.addAction(open_rom_toolbar)
        save_session_toolbar = QAction("Save Session", self)
        save_session_toolbar.triggered.connect(self.save_session)
        toolbar.addAction(save_session_toolbar)

        filter_row = QWidget()
        filter_layout = QHBoxLayout(filter_row)
        filter_layout.setContentsMargins(0, 0, 0, 0)

        self.show_types_button = QPushButton("Show types ▾")
        self.show_types_button.setStyleSheet(DROPDOWN_BUTTON_STYLE)
        self._show_types_menu = QMenu(self)
        self.show_types_button.setMenu(self._show_types_menu)
        self._rebuild_show_types_menu()
        filter_layout.addWidget(self.show_types_button)

        self.filter_box = QLineEdit()
        self.filter_box.setPlaceholderText("Search — path:a/2/3/3, boat | dock, magic:BMD0")
        self.filter_box.textChanged.connect(self._schedule_apply_filter)
        filter_layout.addWidget(self.filter_box, stretch=1)

        self.mapping_box = QComboBox()
        self.mapping_box.addItem("All mapping", "")
        self.mapping_box.addItem("Mapped only", "mapped")
        self.mapping_box.addItem("Unmapped only", "unmapped")
        self.mapping_box.currentIndexChanged.connect(self.apply_filter)
        filter_layout.addWidget(QLabel("Mapping:"))
        filter_layout.addWidget(self.mapping_box)

        self.preset_box = QComboBox()
        self.preset_box.addItem("Recipes…", "")
        self.preset_box.currentIndexChanged.connect(self.apply_selected_preset)
        filter_layout.addWidget(self.preset_box)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(BROWSER_COLUMNS)
        self.tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.tree.itemDoubleClicked.connect(lambda *_: self.preview_selected_asset(manual=True, force=True))
        th = self.tree.header()
        th.setSectionResizeMode(0, QHeaderView.Stretch)
        th.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        th.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        th.setSectionResizeMode(3, QHeaderView.Stretch)

        self.raw_tree = QTreeWidget()
        self.raw_tree.setColumnCount(4)
        self.raw_tree.setHeaderLabels(BROWSER_COLUMNS)
        self.raw_tree.setSelectionMode(QTreeWidget.SingleSelection)
        self.raw_tree.itemSelectionChanged.connect(self.on_selection_changed)
        self.raw_tree.itemExpanded.connect(self._on_tree_item_expanded)
        self.raw_tree.itemDoubleClicked.connect(lambda *_: self.preview_selected_asset(manual=True, force=True))
        raw_th = self.raw_tree.header()
        raw_th.setSectionResizeMode(0, QHeaderView.Stretch)
        raw_th.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        raw_th.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        raw_th.setSectionResizeMode(3, QHeaderView.Stretch)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(FLAT_COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.itemSelectionChanged.connect(self.on_selection_changed)
        self.table.doubleClicked.connect(lambda *_: self.preview_selected_asset(manual=True, force=True))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.Stretch)

        self.browser_tabs = QTabWidget()
        self.browser_tabs.addTab(self.tree, "Mapped Tree")
        self.browser_tabs.addTab(self.raw_tree, "Raw Folders")
        self.browser_tabs.addTab(self.table, "Flat List")
        self.browser_tabs.currentChanged.connect(self._on_browser_tab_changed)

        self.page_prev_button = QPushButton("◀ Previous")
        self.page_prev_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.page_prev_button.clicked.connect(lambda *_: self._change_browser_page(-1))
        self.page_label = QLabel("Page 1 of 1")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_next_button = QPushButton("Next ▶")
        self.page_next_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.page_next_button.clicked.connect(lambda *_: self._change_browser_page(1))
        self.page_summary = QLabel("")
        self.page_summary.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.pagination_row = QWidget()
        pagination_layout = QHBoxLayout(self.pagination_row)
        pagination_layout.setContentsMargins(0, 0, 0, 0)
        pagination_layout.addWidget(self.page_prev_button)
        pagination_layout.addWidget(self.page_label)
        pagination_layout.addWidget(self.page_next_button)
        pagination_layout.addWidget(self.page_summary, stretch=1)

        self.details = QTextEdit()
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(130)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setMinimumHeight(130)
        self.log_box.setStyleSheet("font-family: Menlo, Consolas, monospace; font-size: 11px;")

        self.related_table = QTableWidget(0, 5)
        self.related_table.setHorizontalHeaderLabels(["Relation", "Magic", "Score", "Asset", "Reason"])
        self.related_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.related_table.setSelectionMode(QTableWidget.SingleSelection)
        self.related_table.itemSelectionChanged.connect(self.on_related_selection_changed)
        rel_header = self.related_table.horizontalHeader()
        rel_header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        rel_header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        rel_header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        rel_header.setSectionResizeMode(3, QHeaderView.Stretch)
        rel_header.setSectionResizeMode(4, QHeaderView.Stretch)

        self.info_tabs = QTabWidget()
        self.info_tabs.addTab(self.details, "Details")
        self.info_tabs.addTab(self.related_table, "Related")
        self.info_tabs.addTab(self.log_box, "Terminal")
        info_corner = QWidget()
        info_corner_layout = QHBoxLayout(info_corner)
        info_corner_layout.setContentsMargins(0, 0, 4, 0)
        info_corner_layout.setSpacing(4)
        self.info_copy_button = self._make_icon_tool_button("Copy panel contents to clipboard", "⎘")
        self.info_clear_button = self._make_icon_tool_button("Clear this panel", "⌫")
        self.info_copy_button.clicked.connect(self._copy_info_panel_contents)
        self.info_clear_button.clicked.connect(self._clear_info_panel_contents)
        info_corner_layout.addWidget(self.info_copy_button)
        info_corner_layout.addWidget(self.info_clear_button)
        self.info_tabs.setCornerWidget(info_corner, Qt.TopRightCorner)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(filter_row)
        left_layout.addWidget(self.browser_tabs, stretch=1)
        left_layout.addWidget(self.pagination_row)
        left_layout.addWidget(self.info_tabs)

        self.preview = PreviewWidget()
        self.preview.show_message("")

        self.preview_details = QTextEdit()
        self.preview_details.setReadOnly(True)
        self.preview_details.setMinimumHeight(120)
        self.preview_details.setPlaceholderText("Selection details and texture status will appear here.")

        self.find_texture_button = QPushButton("Set Textures")
        self.find_texture_button.setToolTip("Force a fresh texture resolve for the selected model.")
        self.find_texture_button.clicked.connect(self.find_and_load_texture_for_selected_model)
        self.pin_texture_button = QPushButton("Pin Texture")
        self.pin_texture_button.clicked.connect(self.pin_selected_texture)
        self.clear_pin_button = QPushButton("Clear Pin")
        self.clear_pin_button.clicked.connect(self.clear_pinned_texture)
        self.export_button = QPushButton("Export…")
        self.export_button.setToolTip("Export the selected asset, or export a tree folder as a ZIP when a folder row is selected.")
        self.export_button.clicked.connect(self.export_selected_smart)
        self.preview.action_layout.addWidget(self.find_texture_button)
        self.preview.action_layout.addWidget(self.export_button)

        inspector = QWidget()
        inspector_layout = QVBoxLayout(inspector)
        inspector_layout.setContentsMargins(0, 0, 0, 0)
        inspector_layout.addWidget(self.preview_details, stretch=1)
        pin_row = QWidget()
        pin_layout = QHBoxLayout(pin_row)
        pin_layout.setContentsMargins(0, 0, 0, 0)
        for button in (self.pin_texture_button, self.clear_pin_button):
            pin_layout.addWidget(button)
        pin_layout.addStretch()
        inspector_layout.addWidget(pin_row)

        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.addWidget(self.preview)
        right_splitter.addWidget(inspector)
        right_splitter.setSizes([500, 230])

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left)
        splitter.addWidget(right_splitter)
        splitter.setSizes([820, 520])

        self.setCentralWidget(splitter)
        self.statusBar().showMessage("Ready")

        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.timeout.connect(self._auto_preview_selected)
        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(300)
        self._filter_timer.timeout.connect(self.apply_filter)
        self._filter_worker: FilterWorker | None = None
        self._filter_generation = 0
        self._visible_assets_version = 0
        self._browser_tab_versions: dict[int, int] = {}
        self._asset_search_text: dict[str, str] = {}
        self._mapped_tree_parts_by_id: dict[str, tuple[str, ...]] = {}
        self._raw_tree_parts_by_id: dict[str, tuple[str, ...]] = {}

        preview_menu = menubar.addMenu("&Preview")
        self.texture_action = QAction("Textures", self)
        self.texture_action.setCheckable(True)
        self.texture_action.setChecked(self.preview.textures_toggle.isChecked())
        self.texture_action.setToolTip("Show decoded/applied texture colors in the 3D preview.")
        self.texture_action.toggled.connect(self.preview.textures_toggle.setChecked)
        self.preview.textures_toggle.toggled.connect(self.texture_action.setChecked)
        preview_menu.addAction(self.texture_action)

        self.wireframe_action = QAction("Wireframe", self)
        self.wireframe_action.setCheckable(True)
        self.wireframe_action.setChecked(self.preview.wireframe_toggle.isChecked())
        self.wireframe_action.setToolTip("Draw triangle edges over the preview mesh.")
        self.wireframe_action.toggled.connect(self.preview.wireframe_toggle.setChecked)
        self.preview.wireframe_toggle.toggled.connect(self.wireframe_action.setChecked)
        preview_menu.addAction(self.wireframe_action)

        self._style_chrome_controls()
        self.pagination_row.setVisible(False)
        self._update_pagination_bar()

    def _style_chrome_controls(self) -> None:
        for button in (
            self.page_prev_button,
            self.page_next_button,
            self.info_copy_button,
            self.info_clear_button,
            self.find_texture_button,
            self.export_button,
        ):
            button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.show_types_button.setStyleSheet(DROPDOWN_BUTTON_STYLE)
        for child in self.preview.findChildren(QPushButton):
            if child not in {self.preview.textures_toggle, self.preview.wireframe_toggle}:
                child.setStyleSheet(CHROME_BUTTON_STYLE)

    def _make_icon_tool_button(self, tooltip: str, glyph: str) -> QToolButton:
        button = QToolButton(self)
        button.setToolTip(tooltip)
        button.setText(glyph)
        button.setFixedSize(30, 26)
        button.setStyleSheet(CHROME_BUTTON_STYLE)
        return button

    def _copy_info_panel_contents(self) -> None:
        widget = self.info_tabs.currentWidget()
        text = ""
        if widget in {self.details, self.log_box}:
            text = widget.toPlainText()
        elif widget is self.related_table:
            lines: list[str] = []
            for row in range(self.related_table.rowCount()):
                cols = []
                for col in range(self.related_table.columnCount()):
                    item = self.related_table.item(row, col)
                    cols.append(item.text() if item else "")
                lines.append("\t".join(cols))
            text = "\n".join(lines)
        if text.strip():
            QGuiApplication.clipboard().setText(text)
            self._update_status("Copied panel contents to clipboard.")
        else:
            self._update_status("Nothing to copy in this panel.")

    def _clear_info_panel_contents(self) -> None:
        widget = self.info_tabs.currentWidget()
        if widget is self.details:
            widget.clear()
        elif widget is self.log_box:
            widget.clear()
        elif widget is self.related_table:
            self.related_table.setRowCount(0)
        self._update_status("Cleared the current info panel.")

    def _schedule_apply_filter(self) -> None:
        if hasattr(self, "_filter_timer"):
            self._filter_timer.start()
        else:
            self.apply_filter()

    def _on_type_filter_toggled(self, _checked: bool = False) -> None:
        self._persist_type_filter_preferences()
        self._update_show_types_button_label()
        self._schedule_apply_filter()

    def _type_filter_label(self, magic: str) -> str:
        return TYPE_LABELS.get(magic, magic or "unknown")

    def _ordered_type_magics(self, counts: Counter) -> list[str]:
        known = [magic for magic in TYPE_FILTER_ORDER if counts.get(magic)]
        extra = sorted(m for m in counts if m not in TYPE_FILTER_ORDER)
        return known + extra

    def _type_filter_default_checked(self, magic: str) -> bool:
        if magic in self._type_filter_preferences:
            return self._type_filter_preferences[magic]
        return magic in DEFAULT_TYPE_FILTER_ON

    def _persist_type_filter_preferences(self) -> None:
        for magic, checkbox in self._type_filter_checkboxes.items():
            self._type_filter_preferences[magic] = checkbox.isChecked()

    def _add_type_filter_checkbox(self, magic: str, count: int) -> None:
        label = self._type_filter_label(magic)
        checkbox = QCheckBox(f"{label} ({count:,})")
        checkbox.setChecked(self._type_filter_default_checked(magic))
        checkbox.setToolTip(f"{magic} — {count:,} asset(s) in this ROM")
        checkbox.stateChanged.connect(self._on_type_filter_toggled)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 2, 12, 2)
        row_layout.addWidget(checkbox)
        action = QWidgetAction(self._show_types_menu)
        action.setDefaultWidget(row)
        self._show_types_menu.addAction(action)
        self._type_filter_checkboxes[magic] = checkbox

    def _set_all_type_filters(self, checked: bool) -> None:
        for checkbox in self._type_filter_checkboxes.values():
            checkbox.blockSignals(True)
            checkbox.setChecked(checked)
            checkbox.blockSignals(False)
        self._persist_type_filter_preferences()
        self._update_show_types_button_label()
        self._schedule_apply_filter()

    def _rebuild_show_types_menu(self) -> None:
        if not hasattr(self, "_show_types_menu"):
            return
        self._persist_type_filter_preferences()
        self._show_types_menu.clear()
        self._type_filter_checkboxes = {}

        counts = Counter(asset.magic for asset in self.assets if asset.magic)
        if not counts:
            placeholder = QAction("Load a ROM to see asset types", self)
            placeholder.setEnabled(False)
            self._show_types_menu.addAction(placeholder)
            self._update_show_types_button_label()
            return

        select_all = QAction("Select all", self)
        select_all.triggered.connect(lambda *_: self._set_all_type_filters(True))
        clear_all = QAction("Clear all", self)
        clear_all.triggered.connect(lambda *_: self._set_all_type_filters(False))
        self._show_types_menu.addAction(select_all)
        self._show_types_menu.addAction(clear_all)
        self._show_types_menu.addSeparator()

        placed: set[str] = set()
        for index, (group_label, magics) in enumerate(TYPE_FILTER_GROUPS):
            present = [magic for magic in magics if counts.get(magic)]
            if not present:
                continue
            header = QAction(group_label, self)
            header.setEnabled(False)
            self._show_types_menu.addAction(header)
            for magic in present:
                self._add_type_filter_checkbox(magic, counts[magic])
                placed.add(magic)
            has_later = any(
                counts.get(magic)
                for later_label, later_magics in TYPE_FILTER_GROUPS[index + 1 :]
                for magic in later_magics
            ) or any(magic for magic in self._ordered_type_magics(counts) if magic not in placed)
            if has_later:
                self._show_types_menu.addSeparator()

        remaining = [magic for magic in self._ordered_type_magics(counts) if magic not in placed]
        if remaining:
            header = QAction("Other", self)
            header.setEnabled(False)
            self._show_types_menu.addAction(header)
            for magic in remaining:
                self._add_type_filter_checkbox(magic, counts[magic])

        self._update_show_types_button_label()

    def _update_show_types_button_label(self) -> None:
        if not hasattr(self, "show_types_button"):
            return
        if not self._type_filter_checkboxes:
            self.show_types_button.setText("Show types ▾")
            return
        labels = [
            self._type_filter_label(magic)
            for magic, checkbox in self._type_filter_checkboxes.items()
            if checkbox.isChecked()
        ]
        if not labels:
            self.show_types_button.setText("Show: all types ▾")
        elif len(labels) <= 2:
            self.show_types_button.setText("Show: " + ", ".join(labels) + " ▾")
        else:
            self.show_types_button.setText(f"Show: {len(labels)} types ▾")

    def _selected_mapping_filter(self) -> str:
        if not hasattr(self, "mapping_box"):
            return ""
        return str(self.mapping_box.currentData() or "")

    def _enabled_type_filters(self) -> list[str]:
        enabled = [magic for magic, checkbox in self._type_filter_checkboxes.items() if checkbox.isChecked()]
        return enabled

    def _asset_type_label(self, asset: Asset) -> str:
        return TYPE_LABELS.get(asset.magic, asset.magic or asset.kind or "unknown")

    def _browser_row_values(self, asset: Asset) -> list[str]:
        return [
            self._asset_display_name(asset),
            self._asset_file_label(asset),
            self._asset_type_label(asset),
            asset.virtual_path,
        ]

    def _update_pagination_bar(self) -> None:
        if not hasattr(self, "page_label"):
            return
        on_flat = hasattr(self, "browser_tabs") and self.browser_tabs.currentWidget() is self.table
        self.pagination_row.setVisible(on_flat)
        if not on_flat:
            return
        total = len(self.visible_assets)
        page_size = self.page_size
        page_count = max(1, (total + page_size - 1) // page_size) if total else 1
        page = min(self.browser_page, page_count - 1)
        self.browser_page = page
        start = page * page_size + (1 if total else 0)
        end = min(total, (page + 1) * page_size)
        self.page_label.setText(f"Page {page + 1} of {page_count}")
        if total:
            self.page_summary.setText(f"Showing {start}–{end} of {total:,}")
        else:
            self.page_summary.setText("No matching assets")
        self.page_prev_button.setEnabled(page > 0)
        self.page_next_button.setEnabled(total > 0 and page + 1 < page_count)

    def _change_browser_page(self, delta: int) -> None:
        if not hasattr(self, "browser_tabs") or self.browser_tabs.currentWidget() is not self.table:
            return
        total = len(self.visible_assets)
        page_count = max(1, (total + self.page_size - 1) // self.page_size) if total else 1
        new_page = max(0, min(self.browser_page + delta, page_count - 1))
        if new_page == self.browser_page:
            return
        self.browser_page = new_page
        self._populate_table(self.visible_assets)
        self._update_pagination_bar()

    def _on_browser_tab_changed(self, _index: int) -> None:
        self._populate_current_browser_tab()
        self._update_pagination_bar()

    def _rebuild_asset_filter_indexes(self) -> None:
        self._asset_search_text = {}
        self._mapped_tree_parts_by_id = {}
        self._raw_tree_parts_by_id = {}
        for asset in self.assets:
            self._asset_search_text[asset.asset_id] = asset_search_text(asset)
            self._mapped_tree_parts_by_id[asset.asset_id] = self._tree_parts_for_asset(asset)
            self._raw_tree_parts_by_id[asset.asset_id] = self._raw_tree_parts_for_asset(asset)

    def _compute_filtered_assets(self) -> list[Asset]:
        assets = self.assets
        enabled_types = self._enabled_type_filters()
        if enabled_types:
            allowed = frozenset(enabled_types)
            assets = [asset for asset in assets if asset.magic in allowed]
        mapping_query = self._selected_mapping_filter()
        if mapping_query:
            assets = filter_assets_indexed(assets, mapping_query, self._asset_search_text)
        text_query = self.filter_box.text().strip()
        if text_query:
            assets = filter_assets_indexed(assets, text_query, self._asset_search_text)
        return list(assets)

    def _populate_current_browser_tab(self, *, force: bool = False) -> None:
        if not hasattr(self, "browser_tabs"):
            return
        widget = self.browser_tabs.currentWidget()
        tab_id = id(widget)
        if not force and self._browser_tab_versions.get(tab_id) == self._visible_assets_version:
            return
        if widget is self.table:
            self._populate_table(self.visible_assets)
        elif widget is self.tree:
            self._populate_mapped_tree(self.visible_assets)
        elif widget is self.raw_tree:
            self._populate_raw_tree(self.visible_assets)
        self._browser_tab_versions[tab_id] = self._visible_assets_version

    def _apply_filter_result(self, generation: int, assets: list[Asset]) -> None:
        if generation != self._filter_generation:
            return
        self.visible_assets = assets
        self.browser_page = 0
        self._visible_assets_version += 1
        self._populate_current_browser_tab(force=True)
        self._update_pagination_bar()
        if self.assets:
            self._update_status(f"Showing {len(self.visible_assets):,} of {len(self.assets):,} asset(s).")

    def _clear_texture_caches(self) -> None:
        if self.texture_warmup_worker is not None and self.texture_warmup_worker.isRunning():
            self.texture_warmup_worker.requestInterruption()
        self.texture_warmup_worker = None
        self._texture_library_store.clear()
        self._texture_resolution_cache.clear()

    def _texture_library_for_session(self) -> TextureLibrary | None:
        if not self.assets:
            return None
        if self._texture_library_store.is_ready_for(self.assets):
            return self._texture_library_store.library
        return None

    def _warm_texture_library_async(self) -> None:
        if not self.assets:
            return
        if self._texture_library_store.is_ready_for(self.assets):
            return
        if self.texture_warmup_worker is not None and self.texture_warmup_worker.isRunning():
            return
        self.texture_warmup_worker = TextureLibraryWarmupWorker(self.assets, self._texture_library_store)
        self.texture_warmup_worker.progress.connect(self._update_status)
        self.texture_warmup_worker.finished_ok.connect(self._texture_warmup_finished)
        self.texture_warmup_worker.failed.connect(self._texture_warmup_failed)
        self.texture_warmup_worker.start()

    def _texture_warmup_finished(self) -> None:
        self._update_status("Texture dictionary index is ready for Set Textures.")

    def _texture_warmup_failed(self, message: str) -> None:
        self._update_status(f"Background texture indexing failed: {message}")

    def _texture_resolution_cache_key(self, asset_id: str, pinned_texture_asset_id: str | None) -> str:
        count, digest = self._texture_library_store.fingerprint(self.assets)
        return f"v3:{asset_id}:{pinned_texture_asset_id or ''}:{count}:{digest}"

    def _get_cached_texture_resolution(self, asset_id: str) -> CachedTextureResolution | None:
        key = self._texture_resolution_cache_key(asset_id, self._pinned_texture_asset_id)
        cached = self._texture_resolution_cache.get(key)
        if cached is None:
            return None
        if not cached.preview_path.exists():
            self._texture_resolution_cache.pop(key, None)
            return None
        return cached

    def _store_texture_resolution_cache(
        self,
        asset_id: str,
        *,
        report: str,
        selected_texture_id: str,
        preview_path: Path,
        auxiliary_paths: list[Path],
    ) -> None:
        key = self._texture_resolution_cache_key(asset_id, self._pinned_texture_asset_id)
        self._texture_resolution_cache[key] = CachedTextureResolution(
            report=report,
            selected_texture_id=selected_texture_id,
            preview_path=preview_path,
            auxiliary_paths=list(auxiliary_paths),
        )

    def _roms_directory(self) -> Path:
        roms = project_root() / "roms"
        roms.mkdir(exist_ok=True)
        return roms

    def open_rom(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Open Nintendo DS ROM",
            str(self._roms_directory()),
            "Nintendo DS ROM (*.nds);;All files (*.*)",
        )
        if not path:
            return
        self.rom_path = path
        self.assets = []
        self.visible_assets = []
        self._queued_preview_asset_id = None
        self._last_previewed_asset_id = None
        self._pinned_texture_asset_id = None
        self._clear_texture_caches()
        self._asset_search_text.clear()
        self._mapped_tree_parts_by_id.clear()
        self._raw_tree_parts_by_id.clear()
        self._browser_tab_versions.clear()
        self._filter_generation += 1
        self._name_cache.clear()
        self._display_name_cache.clear()
        self.current_mapping = None
        self.asset_graph = AssetGraph()
        self.assets_by_id = {}
        self._relationship_source_ids = set()
        self._relationship_target_ids = set()
        self.session_path = None
        self._selected_asset_id = None
        self._tree_group_rows = {}
        self._tree_loaded_groups = set()
        self._raw_tree_group_rows = {}
        self._raw_tree_loaded_groups = set()
        self.browser_page = 0
        self._relationship_request_asset_id = None
        if hasattr(self, "preset_box"):
            self.preset_box.setCurrentIndex(0)
        self.table.setRowCount(0)
        if hasattr(self, "tree"):
            self.tree.clear()
        if hasattr(self, "raw_tree"):
            self.raw_tree.clear()
        self.details.clear()
        if hasattr(self, 'related_table'):
            self.related_table.setRowCount(0)
        self.preview.clear()
        self.preview.show_message("Opening ROM...\n\nDSM is building a fast asset index only. Relationship matching, model conversion, and audio expansion run only when you ask for them.")
        mode = "deep" if self.deep_scan_action.isChecked() else "fast"
        self._update_status(f"Fast scanning {path} in {mode} mode. Full relationship graph will not run during load.")

        self.worker = ScanWorker(path, deep_scan=self.deep_scan_action.isChecked())
        self.worker.progress.connect(self._update_status)
        self.worker.finished_ok.connect(self._scan_finished)
        self.worker.failed.connect(self._scan_failed)
        self.worker.start()

    def _scan_finished(self, assets: list[Asset], graph: object = None) -> None:
        self.assets = assets
        self.assets_by_id = {a.asset_id: a for a in assets}
        self.asset_graph = AssetGraph()
        self._rebuild_asset_filter_indexes()
        self._rebuild_show_types_menu()
        self.apply_filter()
        self._warm_texture_library_async()
        bmd_count = sum(1 for a in assets if a.magic == "BMD0")
        texture_count = sum(1 for a in assets if a.magic == "BTX0")
        tile_count = sum(1 for a in assets if a.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR"})
        png_count = sum(1 for a in assets if a.magic == "PNG")
        audio_count = sum(1 for a in assets if a.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"})
        self._load_profile_summary()
        self._populate_search_presets()
        mode = "deep" if self.deep_scan_action.isChecked() else "fast"
        summary = self._session_overview_text(
            source_label=Path(self.rom_path).name if self.rom_path else "Open ROM",
            mode=mode,
            counts=(len(assets), bmd_count, texture_count, tile_count, png_count, audio_count),
        )
        self._update_status(f"Loaded {len(assets)} assets. Details tab has the full profile and counts.")
        self._update_status(self.profile_text or "No game-specific mapping summary was available.")
        self._focus_browser_on_rom_folders()
        if not self.table.selectionModel().selectedRows() and not self.tree.selectedItems():
            self.details.setPlainText(summary)
            self.preview.show_message("Choose an asset to preview or export. Models load with textures automatically when DSM can resolve them.")

    def _session_overview_text(self, *, source_label: str, mode: str, counts: tuple[int, int, int, int, int, int]) -> str:
        total, models, textures, two_d, pngs, audio = counts
        mapping_label = self.current_mapping.mapping_id if self.current_mapping else "none"
        lines = [
            "Session overview",
            f"Source: {source_label}",
            f"Scan mode: {mode}",
            f"Mapping: {mapping_label}",
            "",
            "Detected assets",
            f"  Models: {models}",
            f"  Texture archives: {textures}",
            f"  2D graphics / tiles / fonts: {two_d}",
            f"  PNG images: {pngs}",
            f"  Audio / music / SFX: {audio}",
            f"  Total: {total}",
            "",
            "How to work efficiently",
            "  1. Use the mapped tree or filters to narrow the list.",
            "  2. Browse models with automatic texture preview; use Set Textures to force a refresh or after pinning a BTX0 manually.",
            "  3. Related assets are marked in the browser and listed here in Details.",
            "  4. Export Selected offers the options that make sense for the selected asset.",
            "  5. Save Session writes a self-contained .dsmsession file in saves/ so you can continue later without reopening the ROM.",
        ]
        if self.profile_text:
            lines.extend(["", "Game profile", self.profile_text])
        return "\n".join(lines)

    def _scan_failed(self, message: str) -> None:
        QMessageBox.critical(self, "Scan failed", message)
        self._update_status("Scan failed.")

    def apply_filter(self) -> None:
        if not self.assets:
            self.visible_assets = []
            self._visible_assets_version += 1
            self._populate_current_browser_tab(force=True)
            self._update_pagination_bar()
            return

        self._filter_generation += 1
        generation = self._filter_generation
        enabled_types = self._enabled_type_filters()
        mapping_query = self._selected_mapping_filter()
        text_query = self.filter_box.text().strip()

        if len(self.assets) < 6000:
            self._apply_filter_result(generation, self._compute_filtered_assets())
            return

        if self._filter_worker is not None and self._filter_worker.isRunning():
            self._update_status("Updating filter...")
        else:
            self._update_status(f"Filtering {len(self.assets):,} assets...")
        self._filter_worker = FilterWorker(
            generation,
            self.assets,
            enabled_types=enabled_types,
            mapping_query=mapping_query,
            text_query=text_query,
            search_text_by_id=self._asset_search_text,
        )
        self._filter_worker.finished_ok.connect(self._filter_worker_finished)
        self._filter_worker.failed.connect(self._filter_worker_failed)
        self._filter_worker.start()

    def _filter_worker_finished(self, generation: int, assets: list[Asset]) -> None:
        self._apply_filter_result(generation, assets)

    def _filter_worker_failed(self, generation: int, message: str) -> None:
        if generation != self._filter_generation:
            return
        self._update_status(f"Filter failed: {message}")

    def apply_selected_preset(self) -> None:
        if not hasattr(self, "preset_box"):
            return
        data = self.preset_box.currentData()
        if not data:
            self.filter_box.blockSignals(True)
            self.filter_box.clear()
            self.filter_box.blockSignals(False)
            self.apply_filter()
            return
        query, description, workflow = data
        self.filter_box.blockSignals(True)
        self.filter_box.setText(query)
        self.filter_box.blockSignals(False)
        msg = description
        if workflow:
            msg += "\n" + "\n".join(f"- {step}" for step in workflow)
        self.details.setPlainText(msg)
        self.info_tabs.setCurrentWidget(self.details)
        self._update_status(f"Applied recipe: {self.preset_box.currentText()}")
        self.apply_filter()

    def _populate_search_presets(self) -> None:
        if not hasattr(self, "preset_box"):
            return
        self.preset_box.blockSignals(True)
        self.preset_box.clear()
        self.preset_box.addItem("Recipes…", "")
        mapping = self.current_mapping
        if mapping and getattr(mapping, "search_presets", None):
            for preset in mapping.search_presets:
                if preset.query:
                    self.preset_box.addItem(preset.label or preset.id, (preset.query, preset.description, list(preset.workflow)))
        else:
            self.preset_box.addItem("Battle move effects", ("cat:move-effects|cat:move-animations|path:wazaeffect|path:a/0/2/2|path:a/0/2/3|path:a/0/2/4|path:a/0/2/5|path:a/0/2/9|path:a/0/6/6", "Shows likely move effect graphics, palettes, cells, animations, and scripts.", []))
        self.preset_box.blockSignals(False)

    def _populate_table(self, assets: list[Asset]) -> None:
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        total = len(assets)
        page_count = max(1, (total + self.page_size - 1) // self.page_size) if total else 1
        self.browser_page = min(self.browser_page, page_count - 1)
        start = self.browser_page * self.page_size
        display_assets = assets[start:start + self.page_size]
        self.table.setRowCount(len(display_assets))
        for row, asset in enumerate(display_assets):
            global_index = start + row + 1
            values = [
                str(global_index),
                *self._browser_row_values(asset),
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                source_row = start + row
                item.setData(Qt.UserRole, source_row)
                self._style_table_item(item, asset)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(True)
        self._update_pagination_bar()

    def _unmapped_type_bucket(self, asset: Asset) -> str:
        if asset.magic == "BMD0":
            return "models"
        if asset.magic == "BTX0":
            return "textures"
        if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "PNG", "NFTR"}:
            return "sprites / images / fonts"
        if asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
            return "audio / music / SFX"
        if asset.magic in {"BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}:
            return "model animations"
        return asset.mapping_category or asset.kind or "other"

    def _rom_folder_segments_for_asset(self, asset: Asset) -> tuple[str, ...]:
        folder = (asset.folder_key or "").replace("\\", "/").strip("/")
        if folder:
            return tuple(part for part in folder.split("/") if part)
        vpath = (asset.virtual_path or "").replace("\\", "/").strip("/")
        if not vpath:
            return ()
        parts = [part for part in vpath.split("/") if part]
        if len(parts) <= 1:
            return tuple(parts)
        return tuple(parts[:-1])

    def _initial_rom_folder_segments(self) -> tuple[str, ...]:
        mapping = self.current_mapping
        if mapping and mapping.mapping_id in {"pokemon_bw2", "pokemon_bw"}:
            return ("a",)
        if mapping:
            for archive in mapping.archives:
                path = archive.path.strip("/")
                if path.startswith("a/"):
                    return ("a",)
        counts: Counter[str] = Counter()
        for asset in self.assets:
            segments = self._rom_folder_segments_for_asset(asset)
            if segments:
                counts[segments[0]] += 1
        if not counts:
            return ()
        return (counts.most_common(1)[0][0],)

    def _find_raw_tree_folder_item(self, parts: tuple[str, ...]) -> QTreeWidgetItem | None:
        if not parts or not hasattr(self, "raw_tree"):
            return None
        if len(parts) == 1:
            root = self.raw_tree.invisibleRootItem()
            for i in range(root.childCount()):
                child = root.child(i)
                data = child.data(0, Qt.UserRole)
                if isinstance(data, dict) and tuple(data.get("folder", ())) == parts:
                    return child
            return None
        parent = self._find_raw_tree_folder_item(parts[:-1])
        if parent is None:
            return None
        parent_parts = parts[:-1]
        if parent_parts not in self._raw_tree_loaded_groups:
            self._populate_folder_children(parent, parent_parts, True)
            self._raw_tree_loaded_groups.add(parent_parts)
        if not parent.isExpanded():
            parent.setExpanded(True)
        for i in range(parent.childCount()):
            child = parent.child(i)
            data = child.data(0, Qt.UserRole)
            if isinstance(data, dict) and tuple(data.get("folder", ())) == parts:
                return child
        return None

    def _focus_browser_on_rom_folders(self) -> None:
        if not hasattr(self, "browser_tabs") or not self.visible_assets:
            return
        raw_index = self.browser_tabs.indexOf(self.raw_tree)
        if raw_index < 0:
            return
        self.browser_tabs.setCurrentIndex(raw_index)
        self._populate_raw_tree(self.visible_assets)
        self._browser_tab_versions[id(self.raw_tree)] = self._visible_assets_version

        segments = self._initial_rom_folder_segments()
        if not segments:
            return
        for depth in range(1, len(segments) + 1):
            item = self._find_raw_tree_folder_item(segments[:depth])
            if item is not None:
                item.setExpanded(True)
        final = self._find_raw_tree_folder_item(segments)
        if final is None:
            return
        self.raw_tree.setCurrentItem(final)
        self.raw_tree.scrollToItem(final)
        self.on_selection_changed()

    def _tree_parts_for_asset(self, asset: Asset) -> tuple[str, ...]:
        """Return compact tree folders for an asset.

        Pokémon DS paths like a/0/3/9 used to become four separate folders. For
        browsing, that is just friction, so DSM keeps mapped category/label nodes
        and collapses the real ROM folder into one readable path segment.
        """
        folder = (asset.folder_key or "/").replace("\\", "/").strip("/")
        rom_folder = "/".join(self._rom_folder_segments_for_asset(asset)) or folder or "(rom root)"
        if (asset.mapping_confidence or "") == "format-signature" or not asset.mapping_label or asset.mapping_label == "Detected by signature":
            return ("Unmapped", self._unmapped_type_bucket(asset), rom_folder)
        category = asset.mapping_category or "unknown"
        label = asset.mapping_label or "Detected by signature"
        return (category, label, rom_folder)

    def _raw_tree_parts_for_asset(self, asset: Asset) -> tuple[str, ...]:
        folder_parts = self._rom_folder_segments_for_asset(asset)
        magic = asset.magic or asset.kind or "unknown"
        return (*folder_parts, magic) if folder_parts else (magic,)

    def _populate_mapped_tree(self, assets: list[Asset]) -> None:
        """Build compact mapped-tree folder nodes and add asset leaves lazily."""
        if not hasattr(self, "tree"):
            return
        self.tree.setUpdatesEnabled(False)
        self.tree.blockSignals(True)
        try:
            self.tree.clear()
            self._tree_group_rows = {}
            self._tree_loaded_groups = set()
            folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}

            def get_folder(parts: tuple[str, ...]) -> QTreeWidgetItem:
                if parts in folder_nodes:
                    return folder_nodes[parts]
                parent = self.tree.invisibleRootItem() if len(parts) == 1 else get_folder(parts[:-1])
                item = QTreeWidgetItem([parts[-1], "", "", ""])
                item.setData(0, Qt.UserRole, {"folder": parts})
                parent.addChild(item)
                folder_nodes[parts] = item
                return item

            parts_cache = self._mapped_tree_parts_by_id
            for row, asset in enumerate(assets):
                parts = parts_cache.get(asset.asset_id) or self._tree_parts_for_asset(asset)
                self._tree_group_rows.setdefault(parts, []).append(row)
                get_folder(parts)

            for parts, rows in self._tree_group_rows.items():
                item = folder_nodes.get(parts)
                if item is not None:
                    self._apply_tree_folder_labels(item, parts, rows, raw=False)
                if item is not None and item.childCount() == 0:
                    count = len(rows)
                    placeholder = QTreeWidgetItem([f"Open to load {count} asset(s)", "", "", ""])
                    placeholder.setData(0, Qt.UserRole, {"placeholder": True})
                    item.addChild(placeholder)

            for parts, item in folder_nodes.items():
                self._style_folder_item(item, parts)

            root = self.tree.invisibleRootItem()
            expand_limit = min(root.childCount(), 32)
            for i in range(expand_limit):
                root.child(i).setExpanded(True)
        finally:
            self.tree.blockSignals(False)
            self.tree.setUpdatesEnabled(True)

    def _populate_raw_tree(self, assets: list[Asset]) -> None:
        if not hasattr(self, "raw_tree"):
            return
        self.raw_tree.setUpdatesEnabled(False)
        self.raw_tree.blockSignals(True)
        try:
            self.raw_tree.clear()
            self._raw_tree_group_rows = {}
            self._raw_tree_loaded_groups = set()
            folder_nodes: dict[tuple[str, ...], QTreeWidgetItem] = {}

            def get_folder(parts: tuple[str, ...]) -> QTreeWidgetItem:
                if parts in folder_nodes:
                    return folder_nodes[parts]
                parent = self.raw_tree.invisibleRootItem() if len(parts) == 1 else get_folder(parts[:-1])
                item = QTreeWidgetItem([parts[-1], "", "", ""])
                item.setData(0, Qt.UserRole, {"folder": parts, "raw": True})
                parent.addChild(item)
                folder_nodes[parts] = item
                return item

            parts_cache = self._raw_tree_parts_by_id
            for row, asset in enumerate(assets):
                parts = parts_cache.get(asset.asset_id) or self._raw_tree_parts_for_asset(asset)
                self._raw_tree_group_rows.setdefault(parts, []).append(row)
                get_folder(parts)

            for parts, rows in self._raw_tree_group_rows.items():
                item = folder_nodes.get(parts)
                if item is not None:
                    self._apply_tree_folder_labels(item, parts, rows, raw=True)
                if item is not None and item.childCount() == 0:
                    count = len(rows)
                    placeholder = QTreeWidgetItem([f"Open to load {count} asset(s)", "", "", ""])
                    placeholder.setData(0, Qt.UserRole, {"placeholder": True})
                    item.addChild(placeholder)

            for parts, item in folder_nodes.items():
                self._style_folder_item(item, parts, raw=True)
        finally:
            self.raw_tree.blockSignals(False)
            self.raw_tree.setUpdatesEnabled(True)

    def _on_tree_item_expanded(self, item: QTreeWidgetItem) -> None:
        data = item.data(0, Qt.UserRole)
        if not isinstance(data, dict) or "folder" not in data:
            return
        parts = tuple(data["folder"])
        is_raw = bool(data.get("raw"))
        loaded_groups = self._raw_tree_loaded_groups if is_raw else self._tree_loaded_groups
        if parts in loaded_groups:
            return
        self._populate_folder_children(item, parts, is_raw)
        loaded_groups.add(parts)

    def _populate_folder_children(self, item: QTreeWidgetItem, parts: tuple[str, ...], is_raw: bool) -> None:
        tree = self.raw_tree if is_raw and hasattr(self, "raw_tree") else self.tree
        group_rows = self._raw_tree_group_rows if is_raw else self._tree_group_rows
        rows = group_rows.get(parts, [])
        if not rows:
            return
        tree.blockSignals(True)
        item.takeChildren()
        for source_row in rows:
            if not (0 <= source_row < len(self.visible_assets)):
                continue
            asset = self.visible_assets[source_row]
            leaf = QTreeWidgetItem(self._browser_row_values(asset))
            leaf.setData(0, Qt.UserRole, source_row)
            self._style_tree_asset_item(leaf, asset)
            item.addChild(leaf)
        item.setData(0, Qt.UserRole, {"folder": parts, "raw": is_raw})
        tree.blockSignals(False)
        tree_name = "Raw Folders" if is_raw else "Mapped Tree"
        self._update_status(f"Loaded {len(rows)} asset(s) in {tree_name}: {' / '.join(parts)}.")

    def _relationship_state(self, asset_id: str) -> str:
        if asset_id in self._relationship_source_ids:
            return "built"
        if asset_id in self._relationship_target_ids:
            return "related"
        return ""

    def _style_table_item(self, item: QTableWidgetItem, asset: Asset) -> None:
        state = self._relationship_state(asset.asset_id)
        if state == "built":
            item.setBackground(QBrush(QColor(220, 245, 225)))
        elif state == "related":
            item.setBackground(QBrush(QColor(225, 238, 255)))

    def _style_tree_asset_item(self, item: QTreeWidgetItem, asset: Asset) -> None:
        # Remove any previous relationship prefix before restyling. Relationship
        # marks can be refreshed many times in one session.
        base_text = item.text(0)
        for prefix in ("✓ ", "↳ "):
            if base_text.startswith(prefix):
                base_text = base_text[len(prefix):]
        for col in range(item.columnCount()):
            item.setBackground(col, QBrush())

        state = self._relationship_state(asset.asset_id)
        if state == "built":
            color = QColor(220, 245, 225)
            item.setText(0, "✓ " + base_text)
        elif state == "related":
            color = QColor(225, 238, 255)
            item.setText(0, "↳ " + base_text)
        else:
            item.setText(0, base_text)
            return
        brush = QBrush(color)
        for col in range(item.columnCount()):
            item.setBackground(col, brush)

    def _style_folder_item(self, item: QTreeWidgetItem, parts: tuple[str, ...], *, raw: bool = False) -> None:
        rows = (self._raw_tree_group_rows if raw else self._tree_group_rows).get(parts, [])
        if not rows:
            return
        ids = [self.visible_assets[row].asset_id for row in rows if 0 <= row < len(self.visible_assets)]
        if any(asset_id in self._relationship_source_ids for asset_id in ids):
            item.setForeground(0, QBrush(QColor(20, 110, 45)))
        elif any(asset_id in self._relationship_target_ids for asset_id in ids):
            item.setForeground(0, QBrush(QColor(30, 80, 150)))

    def _refresh_relationship_sets(self) -> None:
        self._relationship_source_ids = {source for source, rows in self.asset_graph.relations.items() if rows}
        targets: set[str] = set()
        for rows in self.asset_graph.relations.values():
            for row in rows:
                targets.add(row.target_id)
        self._relationship_target_ids = targets

    def _refresh_browser_relationship_marks(self) -> None:
        self._refresh_relationship_sets()
        # Do not rebuild the tree here. Rebuilding collapses the user's folder
        # location. Repaint only the currently visible rows/items.
        self._repaint_table_relationship_marks()
        self._repaint_tree_relationship_marks()

    def _repaint_table_relationship_marks(self) -> None:
        for row in range(self.table.rowCount()):
            item0 = self.table.item(row, 0)
            if item0 is None:
                continue
            source_row = item0.data(Qt.UserRole)
            if not isinstance(source_row, int) or not (0 <= source_row < len(self.visible_assets)):
                continue
            asset = self.visible_assets[source_row]
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item is not None:
                    item.setBackground(QBrush())
                    self._style_table_item(item, asset)

    def _repaint_tree_relationship_marks(self) -> None:
        def visit(item: QTreeWidgetItem) -> None:
            data = item.data(0, Qt.UserRole)
            if isinstance(data, int) and 0 <= data < len(self.visible_assets):
                self._style_tree_asset_item(item, self.visible_assets[data])
            elif isinstance(data, dict) and "folder" in data:
                self._style_folder_item(item, tuple(data["folder"]), raw=bool(data.get("raw")))
            for i in range(item.childCount()):
                visit(item.child(i))

        for tree in (self.tree, getattr(self, "raw_tree", None)):
            if tree is None:
                continue
            root = tree.invisibleRootItem()
            for i in range(root.childCount()):
                visit(root.child(i))

    def selected_folder(self) -> tuple[tuple[str, ...], bool] | None:
        if not hasattr(self, "browser_tabs"):
            return None
        active_tree = self.browser_tabs.currentWidget()
        if active_tree not in {self.tree, self.raw_tree}:
            return None
        items = active_tree.selectedItems()
        if not items:
            return None
        data = items[0].data(0, Qt.UserRole)
        if isinstance(data, dict) and "folder" in data:
            return tuple(data["folder"]), bool(data.get("raw"))
        return None

    def _folder_assets(self, parts: tuple[str, ...], raw: bool) -> list[Asset]:
        rows = (self._raw_tree_group_rows if raw else self._tree_group_rows).get(parts, [])
        assets: list[Asset] = []
        for row in rows:
            if 0 <= row < len(self.visible_assets):
                assets.append(self.visible_assets[row])
        return assets

    def selected_asset(self) -> Asset | None:
        # If the user is inspecting the Related tab, keep that related asset active
        # without moving the main mapped tree selection.
        try:
            if hasattr(self, "related_table") and QApplication.focusWidget() is self.related_table and self._selected_asset_id:
                asset = self.assets_by_id.get(self._selected_asset_id)
                if asset is not None:
                    return asset
        except Exception:
            pass
        # Prefer the currently active browser tab. Both the tree and flat list store
        # indexes into visible_assets in Qt.UserRole.
        if hasattr(self, "browser_tabs") and self.browser_tabs.currentWidget() in {self.tree, getattr(self, "raw_tree", None)}:
            active_tree = self.browser_tabs.currentWidget()
            items = active_tree.selectedItems() if active_tree is not None else []
            if items:
                source_row = items[0].data(0, Qt.UserRole)
                if isinstance(source_row, int) and 0 <= source_row < len(self.visible_assets):
                    self._selected_asset_id = self.visible_assets[source_row].asset_id
                    return self.visible_assets[source_row]
        rows = self.table.selectionModel().selectedRows()
        if rows:
            visual_row = rows[0].row()
            item = self.table.item(visual_row, 0)
            if item is not None:
                source_row = item.data(Qt.UserRole)
                if isinstance(source_row, int) and 0 <= source_row < len(self.visible_assets):
                    self._selected_asset_id = self.visible_assets[source_row].asset_id
                    return self.visible_assets[source_row]
        # Fallback to last selected asset id if a folder selection temporarily took focus.
        if self._selected_asset_id:
            return self.assets_by_id.get(self._selected_asset_id)
        return None

    def on_selection_changed(self) -> None:
        folder = self.selected_folder()
        if folder is not None:
            parts, raw = folder
            count = len(self._folder_assets(parts, raw))
            self.details.setPlainText(
                "\n".join([
                    f"Folder: {' / '.join(parts)}",
                    f"Assets: {count}",
                    "",
                    "Use Export… or File → Export Selected to export this folder as a ZIP.",
                ])
            )
            if hasattr(self, "export_button"):
                self.export_button.setEnabled(count > 0)
                self.export_button.setVisible(True)
            if hasattr(self, "find_texture_button"):
                self.find_texture_button.setEnabled(False)
            if hasattr(self, "preview_details"):
                self.preview_details.setPlainText(
                    f"Folder selected: {' / '.join(parts)}\n{count} asset(s). Use Export… to create a ZIP archive."
                )
            return
        self.show_selected_details()
        if self.auto_preview_action.isChecked():
            self._schedule_auto_preview()

    def _schedule_auto_preview(self) -> None:
        if not hasattr(self, "preview_timer"):
            return
        self.preview_timer.start(250)

    def _auto_preview_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            return
        # Single-click preview is the default workflow. Expensive model conversion
        # runs in a worker thread and is queued if another preview is active.
        self.preview_selected_asset(manual=False, force=False)

    def on_related_selection_changed(self) -> None:
        if not hasattr(self, "related_table"):
            return
        rows = self.related_table.selectionModel().selectedRows()
        if not rows:
            return
        item = self.related_table.item(rows[0].row(), 0)
        if item is None:
            return
        asset_id = item.data(Qt.UserRole)
        if isinstance(asset_id, str) and asset_id in self.assets_by_id:
            self._select_asset_by_id(asset_id, preview=True)

    def _select_asset_by_id(self, asset_id: str, *, preview: bool = False) -> None:
        asset = self.assets_by_id.get(asset_id)
        if not asset:
            return
        self._selected_asset_id = asset_id
        # Keep the main folder tree where it is; this method is used from the
        # Related tab so users can inspect a candidate without losing their model.
        self.show_selected_details()
        if preview:
            self.preview_selected_asset(manual=False, force=False)

    def _populate_related_table_for_asset(self, asset: Asset | None) -> None:
        if not hasattr(self, "related_table"):
            return
        self.related_table.blockSignals(True)
        self.related_table.setRowCount(0)
        if not asset or not getattr(self, "asset_graph", None):
            self.related_table.blockSignals(False)
            return
        rows = sorted(self.asset_graph.relations.get(asset.asset_id, []), key=lambda r: (-r.score, r.relation, r.target_id))
        self.related_table.setRowCount(len(rows))
        for row, rel in enumerate(rows):
            target = self.assets_by_id.get(rel.target_id)
            values = [rel.relation, target.magic if target else "?", str(rel.score), target.virtual_path if target else rel.target_id, rel.reason]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                if col == 0:
                    item.setData(Qt.UserRole, rel.target_id)
                if col == 2:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                self.related_table.setItem(row, col, item)
        self.related_table.blockSignals(False)

    def show_selected_details(self) -> None:
        asset = self.selected_asset()
        if not asset:
            self.details.clear()
            self._populate_related_table_for_asset(None)
            return
        details = [
            f"Name: {self._asset_display_name(asset)}",
            f"File: {asset_filename_label(asset.virtual_path)}",
            f"Path: {asset.virtual_path}",
            f"Kind: {asset.kind}",
            f"Magic: {asset.magic}",
            f"Mapping category: {asset.mapping_category or 'unknown'}",
            f"Mapping label: {asset.mapping_label or 'none'}",
            f"Mapping confidence: {asset.mapping_confidence or 'none'}",
            f"Decoded size: {human_size(asset.size)}",
            f"Original size: {human_size(asset.original_size)}",
            f"Compressed: {'yes' if asset.compressed else 'no'}",
            f"Carved from unknown container: {'yes' if asset.carved else 'no'}",
            f"ROM file id: {asset.rom_file_id if asset.rom_file_id is not None else 'unknown'}",
            f"ROM offset: 0x{asset.rom_offset:X}" if asset.rom_offset is not None else "ROM offset: unknown",
        ]
        if asset.carved_offset is not None:
            details.append(f"Carved offset: 0x{asset.carved_offset:X}")
        if asset.container_chain:
            details.append("Containers:")
            details.extend(f"  - {c}" for c in asset.container_chain)
        relation_lines = self.asset_graph.relation_summary(asset.asset_id, self.assets_by_id, limit=18) if getattr(self, "asset_graph", None) else []
        rel_state = self._relationship_state(asset.asset_id)
        details.append(f"Relationships: {'built for this asset' if rel_state == 'built' else 'related to another asset' if rel_state == 'related' else 'not built yet'}")
        if relation_lines:
            details.append("")
            details.append("Related assets")
            details.extend(f"  - {line}" for line in relation_lines)
        elif asset.magic in {"BMD0", "BTX0", "RGCN", "RLCN", "RCSN", "RECN", "RNAN", "SDAT", "SWAR", "SWAV", "STRM", "SSEQ", "SSAR", "SBNK"}:
            details.append("  Set Textures handles model texture decoding; Export Selected… creates focused readable bundles when DSM can pair related files.")

        hunt_report = self._last_texture_resolve_report.get(asset.asset_id)
        if hunt_report:
            details.append("")
            details.append(hunt_report)
        pinned = self._pinned_texture_asset()
        if pinned:
            details.append("")
            details.append(f"Pinned BTX0 texture: {pinned.virtual_path}")

        if asset.magic == "BMD0":
            details.append("")
            details.append("Model preview/export uses apicula.")
            details.append("Use Set Textures to match exact NSBMD material names to NSBTX texture/palette dictionaries while keeping this model selected.")
            details.append("Set Textures writes decoded texture PNGs for the preview/export bundle. Use Selected BTX0 is still available for a manual override.")
            details.append("Progress for model conversion and conversion and texture decoding progress appears in Terminal.")
            if not apicula_available():
                details.append("")
                details.append("Tip: build apicula in tools/apicula/target/release/apicula or set DSM_APICULA to its path.")
        elif asset.magic == "BTX0":
            names = sorted(self._asset_names(asset))
            details.append("")
            details.append(f"Texture/palette names found: {len(names)}")
            if names:
                details.append("  " + ", ".join(names[:40]) + (" ..." if len(names) > 40 else ""))
            details.append("Texture preview/decode runs on a worker thread when you click or auto-preview this row, so large texture archives should not freeze the UI.")
            details.append("Tip: click Use Selected BTX0 to pin this texture for the next BMD0 preview, or Extract Texture PNGs / Export Readable to save PNGs.")
        elif asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR"}:
            details.append("")
            if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
                if asset.size > 8 * 1024 * 1024:
                    previews = []
                    details.append("DSM direct preview images: skipped in Details for large asset; use Preview to decode in a worker.")
                else:
                    try:
                        previews = decode_nitro2d_preview(asset.data, asset.magic)
                    except Exception:
                        previews = []
                    details.append(f"DSM direct preview images: {len(previews)}")
                    for img in previews:
                        details.append(f"  - {img.name}: {img.width}x{img.height} ({img.source})")
                details.append("Tip: Export Selected… offers raw export plus readable PNG/contact sheet output. Use Export Selected… to create a combined readable bundle when DSM can pair the files.")
            else:
                details.append("DSM can identify this asset type. Export Selected saves the raw file.")
        elif asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
            details.append("")
            if asset.magic == "SDAT":
                details.append("SDAT archive: Export Readable writes a lossless audio bundle with child SSEQ/SSAR/SBNK/SWAR/STRM files and WAV previews where DSM can decode samples/streams.")
            elif asset.magic == "SWAR":
                details.append("SWAR sample archive: Export Readable extracts child SWAV samples and WAV previews when possible.")
            elif asset.magic in {"SWAV", "STRM"}:
                details.append("Sample/stream audio: Export Readable writes raw original plus WAV when DSM can decode the payload.")
            else:
                details.append("Sequenced/instrument audio: Export Readable writes the original raw file losslessly. Use VGMTrans/Nitro Studio for MIDI/SF2-style rendering.")
        elif asset.magic == "PNG" or asset.data.startswith(b"\x89PNG"):
            details.append("")
            details.append("PNG preview/export supported directly.")
        self.details.setPlainText("\n".join(details))
        self._populate_related_table_for_asset(asset)
        self._update_preview_details(asset)

    def _preview_result_text(self, path: Path, fallback_textures: list[Path] | None = None) -> str:
        quality = _converted_texture_quality(path)
        fallback_count = len(fallback_textures or [])
        if quality.mesh_faces <= 0:
            return f"Converted file has no visible mesh geometry: {path.name}"
        if quality.confident:
            return f"Preview file: {path.name} — {quality.summary()}."
        if fallback_count:
            return f"Preview file: {path.name} — {quality.summary()}. DSM decoded {fallback_count} texture PNG(s) for preview/export fallback; the GLB itself may still not embed images."
        if quality.weak_material_only:
            return f"Preview file: {path.name} — {quality.summary()}. The converted file does not prove visible texture sampling yet."
        return f"Preview file: {path.name} — mesh faces: {quality.mesh_faces}; no verified texture image detected."

    def _update_preview_details(self, asset: Asset | None = None) -> None:
        if not hasattr(self, "preview_details"):
            return
        asset = asset or self.selected_asset()
        if not asset:
            self.preview_details.setPlainText("No asset selected.")
            self.find_texture_button.setEnabled(False)
            self.export_button.setEnabled(False)
            self.pin_texture_button.setVisible(False)
            self.clear_pin_button.setVisible(False)
            return

        rel_rows = list(getattr(self.asset_graph, "relations", {}).get(asset.asset_id, [])) if getattr(self, "asset_graph", None) else []
        texture_rows = [r for r in rel_rows if r.relation in {"texture", "texture-candidate"}]
        animation_rows = [r for r in rel_rows if "animation" in r.relation or r.relation in {"model-animation", "sprite-animation"}]
        pinned = self._pinned_texture_asset()
        lines = [
            "Selection",
            f"  Name: {self._asset_display_name(asset)}",
            f"  File: {asset_filename_label(asset.virtual_path)}",
            f"  {asset.magic or asset.kind}: {asset.virtual_path}",
            f"  Mapping: {asset.mapping_label or 'unmapped'}",
            f"  Relationships: {len(rel_rows)} found" if rel_rows else "  Relationships: not built yet",
        ]
        if asset.magic == "BMD0":
            lines.append("")
            lines.append("Model texture status")
            lines.append(f"  External texture links: {len(texture_rows)}")
            if texture_rows:
                best = self._best_texture_candidate(asset)
                if best:
                    tex, confidence, reason = best
                    lines.append(f"  Resolved texture: {tex.virtual_path}")
                    lines.append(f"  Confidence: {confidence} — {reason}")
            lines.append(f"  Pinned external texture: {pinned.virtual_path if pinned else 'none'}")
            fallback_count = self._preview_fallback_count_by_asset_id.get(asset.asset_id, 0)
            if fallback_count:
                lines.append(f"  DSM decoded preview/export texture PNGs: {fallback_count}")
            report = self._last_texture_resolve_report.get(asset.asset_id, "")
            if "embedded TEX0 decoded" in report:
                lines.append("  Embedded NSBMD texture: decoded and available as DSM fallback PNGs")
            elif "embedded TEX0 texture block found" in report:
                lines.append("  Embedded NSBMD texture: detected; Set Textures can decode/trace it")
            if animation_rows:
                lines.append(f"  Animation candidates: {len(animation_rows)}")
            status = self._preview_status_by_asset_id.get(asset.asset_id)
            if status:
                lines.append(f"  {status}")
            lines.append("")
            lines.append("Use Set Textures to parse exact NSBMD material names, decode matching NSBTX textures, and preview/export the verified binding.")
        elif asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
            lines.append("")
            lines.append("Sprite/tile status")
            lines.append("  NCGR/RGCN = tile pixels; NCLR/RLCN = palette; NCER/RECN = cell layout; NANR/RNAN = animation timing.")
            lines.append("  Set Textures or Export Selected… to build a focused bundle before preview/export.")
        elif asset.magic in {"SDAT", "SWAR", "SWAV", "STRM", "SSEQ", "SSAR", "SBNK"}:
            lines.append("")
            lines.append("Audio status")
            lines.append("  Export Selected writes the original data and WAV previews where DSM can decode samples/streams.")
        else:
            lines.append("")
            lines.append("Use Preview or Export Selected for the available decoder/export options.")

        self.preview_details.setPlainText("\n".join(lines))
        pinned_active = pinned is not None
        self.export_button.setEnabled(True)
        self.export_button.setVisible(True)
        self.find_texture_button.setEnabled(asset.magic == "BMD0")
        self.find_texture_button.setVisible(True)
        self.pin_texture_button.setEnabled(asset.magic == "BTX0")
        self.pin_texture_button.setVisible(asset.magic == "BTX0")
        self.clear_pin_button.setEnabled(pinned_active)
        self.clear_pin_button.setVisible(pinned_active)


    def build_relationships_for_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select one asset first, then build relationships for it.")
            return
        if self.relationship_worker is not None and self.relationship_worker.isRunning():
            self._update_status("Relationship graph worker is already running; wait for it to finish before starting another.")
            self.info_tabs.setCurrentWidget(self.log_box)
            return
        self.info_tabs.setCurrentWidget(self.log_box)
        self.preview.show_message(
            "Building relationships for selected asset only...\n\n"
            f"{asset.magic} — {asset.virtual_path}\n\n"
            "DSM is matching likely palettes, textures, animations, or audio children without touching the whole ROM graph."
        )
        self._relationship_request_asset_id = asset.asset_id
        self._selected_asset_id = asset.asset_id
        self._update_status(f"Build Relationships started for {asset.virtual_path}")
        self.relationship_worker = RelationshipWorker(
            self.assets,
            [asset.asset_id],
            texture_library=self._texture_library_for_session(),
        )
        self.relationship_worker.progress.connect(self._update_status)
        self.relationship_worker.finished_ok.connect(self._relationships_finished)
        self.relationship_worker.failed.connect(self._relationships_failed)
        self.relationship_worker.start()

    def _relationships_finished(self, graph: object) -> None:
        requested_id = self._relationship_request_asset_id
        requested_asset = self.assets_by_id.get(requested_id or "")
        if requested_id:
            self._selected_asset_id = requested_id

        if isinstance(graph, AssetGraph):
            self.asset_graph.merge(graph)
            rel_count = sum(len(v) for v in graph.relations.values())
            self._refresh_browser_relationship_marks()
            self._update_status(f"Relationships ready: {rel_count} edge(s) merged. The current tree location was preserved.")
        else:
            self._update_status("Build Relationships finished, but produced no graph object.")

        self.show_selected_details()
        current = requested_asset or self.selected_asset()
        if current and current.magic == "BMD0":
            self._apply_relationship_texture_for_model(current)
        elif current and current.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
            self._preview_decodable_images(current, current.kind)

    def _apply_relationship_texture_for_model(self, asset: Asset) -> None:
        best = self._best_texture_candidate(asset)
        if best is None:
            report = self._last_texture_resolve_report.get(asset.asset_id, "")
            if "embedded TEX0 decoded" in report or "embedded TEX0 texture block found" in report:
                self._update_status("This model uses embedded NSBMD texture data; no external BTX0 needs to be pinned. Set Textures shows the texture decode trace.")
            else:
                self._update_status("No exact external BTX0 texture binding was identified for this model. If it still looks gray, use Set Textures for the trace or pin a BTX0 manually as an override.")
            return
        tex, confidence, reason = best
        self._pinned_texture_asset_id = tex.asset_id
        self._update_status(f"Selected resolved texture for model: {tex.virtual_path} ({confidence}; {reason}).")
        if apicula_available():
            self._update_status("Rebuilding the model preview with the resolved texture applied first.")
            self.preview_selected_asset(manual=True, force=True)
        else:
            self._update_status("apicula is not available, so DSM pinned the texture but could not rebuild the model preview.")

    def _best_texture_candidate(self, asset: Asset) -> tuple[Asset, str, str] | None:
        rows = list(getattr(self.asset_graph, "relations", {}).get(asset.asset_id, []))
        texture_rows = [r for r in rows if r.relation in {"texture", "texture-candidate"}]
        if not texture_rows:
            return None
        texture_rows.sort(key=lambda r: (0 if r.relation == "texture" else 1, -r.score, r.target_id))
        for rel in texture_rows:
            tex = self.assets_by_id.get(rel.target_id)
            if tex and tex.magic == "BTX0":
                confidence = "exact decoded binding" if rel.relation == "texture" else "manual/debug candidate"
                return tex, confidence, rel.reason
        return None

    def _relationships_failed(self, message: str) -> None:
        self.preview.show_message(f"Relationship graph failed:\n{message}")
        self._update_status(f"Build Relationships failed: {message}")

    def find_and_load_texture_for_selected_model(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BMD0/NSBMD model first.")
            return
        if asset.magic != "BMD0":
            QMessageBox.information(self, "Not a model", "Set Textures works on BMD0/NSBMD model assets. Select a model first.")
            return
        if not apicula_available():
            QMessageBox.warning(self, "apicula not found", apicula_help_text())
            return
        self._preview_model_with_textures(asset, manual=True, force=True, switch_to_details=True)

    def _apply_texture_resolution_cache(self, asset: Asset, cached: CachedTextureResolution, *, switch_to_details: bool) -> None:
        self._last_texture_resolve_report[asset.asset_id] = cached.report
        if cached.selected_texture_id:
            self._pinned_texture_asset_id = cached.selected_texture_id
        self._selected_asset_id = asset.asset_id
        self.preview.load_glb(cached.preview_path, fallback_textures=cached.auxiliary_paths)
        self._last_previewed_asset_id = asset.asset_id
        self._preview_fallback_count_by_asset_id[asset.asset_id] = len(cached.auxiliary_paths)
        self._preview_status_by_asset_id[asset.asset_id] = self._preview_result_text(cached.preview_path, cached.auxiliary_paths)
        self._update_status(f"Previewing cached textured model: {asset.virtual_path}")
        self._update_preview_details(asset)
        if switch_to_details:
            self.show_selected_details()
            self.info_tabs.setCurrentWidget(self.details)

    def _start_texture_resolve_worker(self, asset: Asset) -> None:
        graph_related = self._graph_related_assets(asset, limit=48)
        out_dir = self.preview_temp / asset.asset_id / "texture_resolve"
        self.texture_resolve_worker = TextureResolveWorker(
            asset,
            out_dir,
            self.assets,
            self._pinned_texture_asset_id,
            graph_related_assets=graph_related,
            texture_library=self._texture_library_for_session(),
            texture_store=self._texture_library_store,
        )
        self.texture_resolve_worker.progress.connect(self._update_status)
        self.texture_resolve_worker.finished_ok.connect(self._texture_resolve_finished)
        self.texture_resolve_worker.failed.connect(self._texture_resolve_failed)
        self.texture_resolve_worker.start()

    def _preview_model_with_textures(
        self,
        asset: Asset,
        *,
        manual: bool,
        force: bool,
        switch_to_details: bool = False,
    ) -> None:
        if asset.magic != "BMD0":
            if manual:
                QMessageBox.information(self, "Not a model", "Preview works on BMD0/NSBMD model assets.")
            return
        if not apicula_available():
            if manual:
                QMessageBox.warning(self, "apicula not found", apicula_help_text())
            else:
                self.preview.show_message(apicula_help_text())
                self._update_status("apicula was not found, so DSM can list/export but not preview models yet.")
            return

        if self.texture_resolve_worker is not None and self.texture_resolve_worker.isRunning():
            self._queued_preview_asset_id = asset.asset_id
            self._update_status(f"Queued textured preview for {asset.virtual_path}...")
            return

        if not force:
            cached = self._get_cached_texture_resolution(asset.asset_id)
            if cached is not None:
                self._apply_texture_resolution_cache(asset, cached, switch_to_details=switch_to_details)
                return

        self._selected_asset_id = asset.asset_id
        self._texture_preview_switch_to_details = switch_to_details
        if switch_to_details:
            self.info_tabs.setCurrentWidget(self.log_box)
        pinned = self._pinned_texture_asset()
        pin_note = f"\nPinned texture: {pinned.virtual_path}" if pinned else ""
        self.preview.show_message(
            f"Previewing model with textures...\n\n{asset.virtual_path}{pin_note}\n\n"
            "DSM is matching NSBMD materials to NSBTX dictionaries and converting the preview."
        )
        self._update_status(f"Previewing textured model: {asset.virtual_path}")
        self._start_texture_resolve_worker(asset)

    def _finish_texture_preview_queue(self, completed_asset_id: str) -> None:
        queued = self._queued_preview_asset_id
        self._queued_preview_asset_id = None
        current = self.selected_asset()
        if queued and current and current.asset_id == queued and current.asset_id != completed_asset_id:
            self._schedule_auto_preview()

    def _texture_resolve_finished(self, asset_id: str, result: object, texture_asset_id: str, report: str) -> None:
        self._last_texture_resolve_report[asset_id] = report
        if texture_asset_id:
            self._pinned_texture_asset_id = texture_asset_id
            tex = self.assets_by_id.get(texture_asset_id)
            if tex and tex.magic == "BTX0":
                self._update_status(f"Texture resolve selected external texture archive {tex.virtual_path} for future previews/exports.")
        elif "embedded TEX0" in report:
            self._update_status("Texture resolve used embedded NSBMD texture data; no external texture archive was pinned.")
        current = self.assets_by_id.get(asset_id)
        if current:
            self._selected_asset_id = asset_id
        if getattr(result, "output_files", None):
            first = _best_preview_path(result.output_files) or result.output_files[0]
            fallback_paths = list(getattr(result, "auxiliary_files", []))
            self._store_texture_resolution_cache(
                asset_id,
                report=report,
                selected_texture_id=texture_asset_id,
                preview_path=first,
                auxiliary_paths=fallback_paths,
            )
            self.preview.load_glb(first, fallback_textures=fallback_paths)
            self._last_previewed_asset_id = asset_id
            self._preview_fallback_count_by_asset_id[asset_id] = len(fallback_paths)
            self._preview_status_by_asset_id[asset_id] = self._preview_result_text(first, fallback_paths)
            quality = _converted_texture_quality(first)
            if quality.confident:
                self._update_status(f"Previewing {first}. {quality.summary()}.")
            elif fallback_paths:
                self._update_status(f"Previewing {first} with {len(fallback_paths)} decoded texture PNG fallback(s).")
            else:
                self._update_status(f"Previewing {first}. No verified texture images were decoded.")
            self._update_preview_details(current)
        switch_to_details = getattr(self, "_texture_preview_switch_to_details", False)
        self._texture_preview_switch_to_details = False
        if switch_to_details:
            self.show_selected_details()
            self.info_tabs.setCurrentWidget(self.details)

        self._finish_texture_preview_queue(asset_id)

    def _texture_resolve_failed(self, asset_id: str, message: str) -> None:
        self._last_texture_resolve_report[asset_id] = "Texture resolve report\n" + message
        current = self.selected_asset()
        if current and current.asset_id == asset_id:
            self._update_status("Textured preview failed; falling back to geometry-only preview.")
            self._start_geometry_preview(current, manual=False)
        else:
            self.preview.show_message(f"Textured preview failed:\n{message}")
            self._update_status(f"Textured preview failed: {message}")
        self._texture_preview_switch_to_details = False
        self._finish_texture_preview_queue(asset_id)
        self.show_selected_details()

    def pin_selected_texture(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BTX0/NSBTX texture asset first.")
            return
        if asset.magic != "BTX0":
            QMessageBox.information(self, "Not a texture", "Select a BTX0/NSBTX texture row first. Try filtering for BTX0 or a/0/1/4.")
            return
        self._pinned_texture_asset_id = asset.asset_id
        self.show_selected_details()
        self._update_status(f"Pinned texture archive for previews/exports: {asset.virtual_path}")

    def clear_pinned_texture(self) -> None:
        self._pinned_texture_asset_id = None
        self.show_selected_details()
        self._update_status("Cleared pinned texture archive.")

    def _pinned_texture_asset(self) -> Asset | None:
        if not self._pinned_texture_asset_id:
            return None
        for asset in self.assets:
            if asset.asset_id == self._pinned_texture_asset_id and asset.magic == "BTX0":
                return asset
        return None

    def extract_selected_texture_images(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BTX0/NSBTX texture asset first.")
            return
        if asset.magic != "BTX0":
            QMessageBox.information(self, "Not a texture", "Select a BTX0/NSBTX texture row first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose texture image export folder")
        if not out_dir:
            return
        base = Path(out_dir) / f"dsm_textures_{asset.asset_id}"
        self._update_status(f"Extracting texture images from {asset.virtual_path}...")

        # v8 first tries DSM's own NSBTX decoder. It is faster and does not need
        # apicula for common indexed/direct DS texture formats.
        try:
            decoded = decode_btx_images(asset.data, max_images=256, mode="all-palettes")
            written = export_readable_asset(asset, out_dir) if decoded else []
        except Exception:
            decoded = []
            written = []
        if written:
            QMessageBox.information(self, "Textures extracted", f"DSM decoded and wrote {len(written)} PNG file(s) to:\n{Path(out_dir)}")
            self._update_status(f"DSM decoded {len(written)} texture PNG(s) to {out_dir}")
            return

        # Fallback: apicula may still be useful for weird model/texture cases.
        if not apicula_available():
            QMessageBox.warning(self, "Texture extraction incomplete", "DSM could not decode this BTX0 directly, and apicula was not found for fallback extraction.\n\n" + apicula_help_text())
            self._update_status("Texture extraction failed: no direct decode and no apicula fallback.")
            return
        result = convert_texture_with_apicula(asset, base)
        if result.ok:
            images = texture_outputs(base)
            QMessageBox.information(self, "Textures extracted", f"apicula wrote {len(images)} image/file(s) to:\n{base}")
            self._update_status(f"apicula extracted {len(images)} texture image/file(s) to {base}")
        else:
            QMessageBox.warning(self, "Texture extraction failed", result.message)
            self._update_status("Texture extraction failed.")


    def preview_selected_asset(self, *, manual: bool = True, force: bool = False) -> None:
        asset = self.selected_asset()
        if not asset:
            if manual:
                QMessageBox.information(self, "No selection", "Select an asset first.")
            return
        if asset.magic == "BMD0":
            self.convert_preview_selected(manual=manual, force=force)
            return
        if asset.magic == "BTX0":
            self._preview_decodable_images(asset, "BTX0 texture archive")
            return
        if asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
            self._preview_decodable_images(asset, asset.kind)
            return
        if asset.magic in {"SDAT", "SWAR", "SWAV", "STRM", "SSEQ", "SSAR", "SBNK"}:
            self.preview.show_message(
                f"Audio asset selected. Use Export Selected… to write raw files and WAV previews where possible.\n\n{asset.virtual_path}"
            )
            self._update_status(f"Audio selected: {asset.virtual_path}")
            return
        if asset.magic == "PNG" or asset.data.startswith(b"\x89PNG"):
            out = self.preview_temp / f"{asset.asset_id}.png"
            out.write_bytes(asset.data)
            self.preview.show_image_path(out, f"PNG image: {asset.virtual_path}")
            self._update_status(f"Previewing PNG image {asset.virtual_path}")
            return
        if manual:
            QMessageBox.information(self, "No visual decoder yet", f"DSM can export this asset raw, but does not have a visual preview for {asset.magic or asset.kind} yet.")
        else:
            self.preview.show_message(f"No visual preview decoder yet for this asset.\n\n{asset.kind} / {asset.magic}\n{asset.virtual_path}")

    def _quick_related_2d_assets(self, asset: Asset, *, limit: int = 16) -> list[Asset]:
        """Cheap same-folder fallback so NCER/NANR can preview without a full graph.

        Nitro 2D files rarely carry friendly cross-file names. The game usually
        loads a small group by archive/table context, so for browsing we rank
        assets in the same folder/container by numeric file index and choose the
        nearest graphics, palette, cell, screen, and animation partners.
        """
        wanted = {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}
        source_num = self._asset_numeric_tail(asset)
        ranked: list[tuple[int, int, str, Asset]] = []
        for candidate in self.assets:
            if candidate.asset_id == asset.asset_id or candidate.magic not in wanted:
                continue
            same_folder = candidate.folder_key == asset.folder_key
            same_container = bool(asset.container_chain and candidate.container_chain and asset.container_chain[-1:] == candidate.container_chain[-1:])
            same_category = bool(asset.mapping_category and asset.mapping_category == candidate.mapping_category)
            if not (same_folder or same_container or same_category):
                continue
            cand_num = self._asset_numeric_tail(candidate)
            distance = abs(cand_num - source_num) if cand_num is not None and source_num is not None else 9999
            context = 0 if same_folder else (1 if same_container else 2)
            ranked.append((context, distance, candidate.virtual_path, candidate))
        ranked.sort(key=lambda item: (item[0], item[1], item[2]))

        # Keep a balanced set instead of returning the first N, which often means
        # many palettes but no tiles/cells.
        out: list[Asset] = []
        seen: set[str] = set()
        per_magic: dict[str, int] = {}
        for _context, _distance, _path, candidate in ranked:
            count = per_magic.get(candidate.magic, 0)
            if count >= 4:
                continue
            out.append(candidate)
            seen.add(candidate.asset_id)
            per_magic[candidate.magic] = count + 1
            if len(out) >= limit:
                break
        # Ensure at least one of each nearby core type where possible.
        for magic in ("RGCN", "RLCN", "RCSN", "RECN", "RNAN"):
            if any(a.magic == magic for a in out):
                continue
            for _context, _distance, _path, candidate in ranked:
                if candidate.magic == magic and candidate.asset_id not in seen:
                    out.append(candidate)
                    seen.add(candidate.asset_id)
                    break
        return out[:limit]

    def _asset_numeric_tail(self, asset: Asset) -> int | None:
        nums = re.findall(r"\d+", asset.virtual_path)
        return int(nums[-1]) if nums else None

    def _preview_decodable_images(self, asset: Asset, label: str) -> None:
        if self.image_preview_worker is not None and self.image_preview_worker.isRunning():
            self._update_status(f"Image preview already running; queued selection will preview after it finishes if selected again.")
            return
        out = self.preview_temp / f"preview_{asset.asset_id}.png"
        related = self._graph_related_assets(asset, limit=16)
        if not related and asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}:
            related = self._quick_related_2d_assets(asset, limit=16)
        related_note = f"\nUsing {len(related)} related asset(s)." if related else ""
        self.preview.show_message(f"Decoding preview off the UI thread...\n{asset.virtual_path}{related_note}")
        self._update_status(f"Starting preview decode for {asset.virtual_path}; related assets: {len(related)}")
        self.image_preview_worker = ImagePreviewWorker(asset, out, label, related_assets=related)
        self.image_preview_worker.progress.connect(self._update_status)
        self.image_preview_worker.finished_ok.connect(self._image_preview_finished)
        self.image_preview_worker.failed.connect(self._image_preview_failed)
        self.image_preview_worker.start()

    def _image_preview_finished(self, asset_id: str, path: str, caption: str) -> None:
        current = self.selected_asset()
        if current and current.asset_id == asset_id:
            self.preview.show_image_path(Path(path), caption)
            self._update_status(f"Preview decoded: {Path(path).name}")
        else:
            self._update_status("Preview decode finished for a previously selected row.")

    def _image_preview_failed(self, asset_id: str, message: str) -> None:
        current = self.selected_asset()
        if current and current.asset_id == asset_id:
            self.preview.show_message(
                f"DSM found this asset, but could not decode a preview image yet.\n\n"
                f"{current.virtual_path}\n\n"
                f"{message}\n\n"
                "Use Export Selected for raw data, readable PNG/WAV outputs, or a model/audio bundle when available."
            )
        self._update_status(f"Preview decode failed: {message}")

    def save_session(self) -> None:
        if not self.assets:
            QMessageBox.information(self, "Nothing to save", "Open a ROM or session before saving your work.")
            return
        if self.session_save_worker is not None and self.session_save_worker.isRunning():
            QMessageBox.information(self, "Session save running", "DSM is already saving a session. Progress is shown in Terminal.")
            self.info_tabs.setCurrentWidget(self.log_box)
            return
        saves_dir = Path.cwd() / "saves"
        saves_dir.mkdir(exist_ok=True)
        base_name = Path(self.rom_path).stem if self.rom_path else "dsm_session"
        safe_base = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in base_name)[:64] or "dsm_session"
        suggested = saves_dir / f"{safe_base}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.dsmsession"
        target, _ = QFileDialog.getSaveFileName(self, "Save DSM session", str(suggested), "DSM session (*.dsmsession);;Zip archive (*.zip)")
        if not target:
            return
        mapping_id = self.current_mapping.mapping_id if self.current_mapping else ""
        self.info_tabs.setCurrentWidget(self.log_box)
        self._update_status("Saving session. This writes extracted asset data into saves/ and may take a moment for large ROMs.")
        self.session_save_worker = SessionSaveWorker(
            Path(target),
            self.assets,
            self.asset_graph,
            rom_path=self.rom_path,
            profile_text=self.profile_text,
            mapping_id=mapping_id,
            pinned_texture_asset_id=self._pinned_texture_asset_id,
        )
        self.session_save_worker.progress.connect(self._update_status)
        self.session_save_worker.finished_ok.connect(self._session_save_finished)
        self.session_save_worker.failed.connect(self._session_save_failed)
        self.session_save_worker.start()

    def _session_save_finished(self, path: str) -> None:
        self.session_path = path
        QMessageBox.information(self, "Session saved", f"Saved DSM session:\n{path}")
        self._update_status(f"Session saved: {path}")

    def _session_save_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Session save failed", message)
        self._update_status(f"Session save failed: {message}")

    def open_session(self) -> None:
        source, _ = QFileDialog.getOpenFileName(self, "Open DSM session", str(Path.cwd() / "saves"), "DSM session (*.dsmsession *.zip);;All files (*.*)")
        if not source:
            return
        if self.session_load_worker is not None and self.session_load_worker.isRunning():
            QMessageBox.information(self, "Session load running", "DSM is already opening a session. Progress is shown in Terminal.")
            self.info_tabs.setCurrentWidget(self.log_box)
            return
        self.info_tabs.setCurrentWidget(self.log_box)
        self.preview.show_message("Opening saved session...\n\nDSM will restore the asset index and saved relationship graph without reading the original ROM.")
        self._update_status(f"Opening session {source}")
        self.session_load_worker = SessionLoadWorker(Path(source))
        self.session_load_worker.progress.connect(self._update_status)
        self.session_load_worker.finished_ok.connect(self._session_load_finished)
        self.session_load_worker.failed.connect(self._session_load_failed)
        self.session_load_worker.start()

    def _session_load_finished(self, payload: object) -> None:
        data = payload if isinstance(payload, dict) else {}
        self._clear_texture_caches()
        self.rom_path = None
        self.session_path = str(data.get("path", ""))
        self.assets = list(data.get("assets", []))
        self.assets_by_id = {a.asset_id: a for a in self.assets}
        self.asset_graph = data.get("graph") if isinstance(data.get("graph"), AssetGraph) else AssetGraph()
        self._refresh_relationship_sets()
        manifest = data.get("manifest", {}) if isinstance(data.get("manifest"), dict) else {}
        self.profile_text = str(manifest.get("profile_text", ""))
        self._pinned_texture_asset_id = manifest.get("pinned_texture_asset_id") or None
        self.current_mapping = None
        self._selected_asset_id = None
        self._queued_preview_asset_id = None
        self._last_previewed_asset_id = None
        self._name_cache.clear()
        self._display_name_cache.clear()
        self._tree_group_rows = {}
        self._tree_loaded_groups = set()
        self._raw_tree_group_rows = {}
        self._raw_tree_loaded_groups = set()
        self.browser_page = 0
        self.table.setRowCount(0)
        self.tree.clear()
        self._rebuild_asset_filter_indexes()
        self._rebuild_show_types_menu()
        self.apply_filter()
        self._warm_texture_library_async()
        self._focus_browser_on_rom_folders()
        total = len(self.assets)
        bmd_count = sum(1 for a in self.assets if a.magic == "BMD0")
        texture_count = sum(1 for a in self.assets if a.magic == "BTX0")
        tile_count = sum(1 for a in self.assets if a.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR"})
        png_count = sum(1 for a in self.assets if a.magic == "PNG")
        audio_count = sum(1 for a in self.assets if a.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"})
        overview = self._session_overview_text(
            source_label=Path(self.session_path).name if self.session_path else "Saved session",
            mode="session",
            counts=(total, bmd_count, texture_count, tile_count, png_count, audio_count),
        )
        self.details.setPlainText(overview)
        self.preview.show_message("Session loaded. Select an asset to preview, build relationships, or export selected data.")
        self._update_status(f"Session loaded: {total} assets restored. Original ROM is not required for this session.")

    def _session_load_failed(self, message: str) -> None:
        QMessageBox.warning(self, "Session load failed", message)
        self._update_status(f"Session load failed: {message}")

    def export_selected_smart(self) -> None:
        folder = self.selected_folder()
        if folder is not None:
            self._export_selected_folder(folder)
            return
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(
                self,
                "No selection",
                "Select an asset row, or select a folder in Mapped Tree / Raw Folders to export.",
            )
            return
        options = self._export_options_for(asset)
        choice = self._choose_export_option(asset, options)
        if not choice:
            return
        default_dir = Path.cwd() / "exports"
        default_dir.mkdir(exist_ok=True)
        out_dir = QFileDialog.getExistingDirectory(self, "Choose export folder", str(default_dir))
        if not out_dir:
            return
        out = Path(out_dir)
        self.info_tabs.setCurrentWidget(self.log_box)
        self._update_status(f"Export Selected: {choice} for {asset.virtual_path}")
        try:
            written = self._run_export_choice(asset, choice, out)
        except Exception as exc:
            QMessageBox.warning(self, "Export failed", str(exc))
            self._update_status(f"Export failed: {exc}")
            return
        if written:
            msg = f"Exported {len(written)} file(s) to:\n{out}"
        else:
            msg = f"Export finished to:\n{out}\n\nNo readable derived files were produced, but check raw outputs/logs if applicable."
        QMessageBox.information(self, "Export complete", msg)
        self._update_status(msg.replace("\n", " "))

    def _folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            ("folder_readable", "ZIP: Readable PNGs / previews", "Decode textures, tiles, palettes, and PNGs into a portable archive."),
            ("folder_raw", "ZIP: Raw original files", "Write the extracted Nitro payloads using their virtual ROM paths."),
            ("folder_glb", "ZIP: GLB models (BMD0 only)", "Convert every visible model in the folder to GLB via apicula."),
            ("folder_mixed", "ZIP: Mixed smart bundle", "Raw files plus readable previews and GLB models where DSM can produce them."),
        ]

    def _export_selected_folder(self, folder: tuple[tuple[str, ...], bool]) -> None:
        parts, raw = folder
        assets = self._folder_assets(parts, raw)
        if not assets:
            QMessageBox.information(self, "Empty folder", "This folder has no visible assets to export.")
            return
        options = self._folder_export_options()
        choice = self._choose_export_option_dialog(
            "Export Folder",
            f"Folder: {' / '.join(parts)}\nAssets in folder: {len(assets)}",
            options,
        )
        if not choice:
            return
        if choice in {"folder_glb", "folder_mixed"} and not apicula_available():
            QMessageBox.warning(self, "apicula not found", apicula_help_text())
            return
        default_dir = Path.cwd() / "exports"
        default_dir.mkdir(exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", parts[-1] if parts else "folder").strip("_") or "folder"
        out_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save folder export ZIP",
            str(default_dir / f"folder_{safe_name}.zip"),
            "ZIP archive (*.zip)",
        )
        if not out_path:
            return
        zip_path = Path(out_path)
        if zip_path.suffix.lower() != ".zip":
            zip_path = zip_path.with_suffix(".zip")
        self.info_tabs.setCurrentWidget(self.log_box)
        self._update_status(f"Exporting {len(assets)} asset(s) from {' / '.join(parts)} to {zip_path.name}...")
        try:
            file_count, errors = self._build_folder_zip(assets, parts, choice, zip_path)
        except Exception as exc:
            QMessageBox.warning(self, "Folder export failed", str(exc))
            self._update_status(f"Folder export failed: {exc}")
            return
        msg = f"Exported {file_count} file(s) to:\n{zip_path}"
        if errors:
            msg += f"\n\n{len(errors)} asset(s) had errors. See folder_export_log.txt inside the ZIP."
        QMessageBox.information(self, "Folder export complete", msg)
        self._update_status(msg.replace("\n", " "))

    def _build_folder_zip(
        self,
        assets: list[Asset],
        parts: tuple[str, ...],
        mode: str,
        zip_path: Path,
    ) -> tuple[int, list[str]]:
        errors: list[str] = []
        written_files = 0
        with tempfile.TemporaryDirectory(prefix="dsm_folder_export_") as tmp:
            staging = Path(tmp)
            manifest = {
                "format": "dsm-folder-export-v1",
                "folder": list(parts),
                "mode": mode,
                "asset_count": len(assets),
                "exported_at": datetime.now(timezone.utc).isoformat(),
            }
            total = len(assets)
            for index, asset in enumerate(assets, start=1):
                self._update_status(f"Folder export {index}/{total}: {asset.virtual_path}")
                try:
                    written_files += self._export_folder_asset(asset, mode, staging)
                except Exception as exc:
                    errors.append(f"{asset.virtual_path}: {exc}")
            manifest["errors"] = errors
            (staging / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            if errors:
                log = staging / "folder_export_log.txt"
                log.write_text("\n".join(errors), encoding="utf-8")
            written_files += 1
            file_count = archive_directory_as_zip(staging, zip_path)
        return file_count, errors

    def _export_folder_asset(self, asset: Asset, mode: str, staging: Path) -> int:
        count = 0
        if mode in {"folder_raw", "folder_mixed"}:
            export_asset(asset, staging / "raw", decoded=True)
            count += 1
        if mode in {"folder_readable", "folder_mixed"}:
            readable = export_readable_asset(asset, staging / "readable" / asset.asset_id)
            count += len(readable)
        if mode in {"folder_glb", "folder_mixed"} and asset.magic == "BMD0":
            related = self._model_related_assets(asset)[:32]
            out_dir = staging / "glb" / Path(asset.virtual_path).stem
            out_dir.mkdir(parents=True, exist_ok=True)
            result = convert_with_apicula(asset, out_dir, sibling_assets=related, output_format="glb")
            if not result.ok:
                raise RuntimeError(result.message)
            count += len(result.output_files or [])
        return count

    def _export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        options: list[tuple[str, str, str]] = [("raw", "Original / raw asset", "Save exactly this selected asset as DSM extracted it.")]
        if asset.magic == "BMD0":
            options.extend([
                ("model_glb", "Model: GLB via apicula", "Convert selected model with graph-related textures/animations supplied to apicula."),
                ("model_dae", "Model: DAE / Collada via apicula", "Useful for Blender import and debugging material names."),
                ("model_obj", "Model: OBJ + MTL via GLB bridge", "Experimental: converts GLB output to OBJ/MTL using trimesh."),
                ("model_bundle", "Model: full research bundle", "Raw model, related BTX0/animations, decoded texture PNGs, GLB, DAE, reports."),
            ])
        elif asset.magic == "BTX0":
            options.extend([
                ("readable", "Texture PNGs/contact sheet", "Decode NSBTX/BTX0 textures to PNG when DSM supports the format."),
                ("texture_apicula", "Texture extraction via apicula fallback", "Try apicula's texture extraction for unusual BTX0 cases."),
            ])
        elif asset.magic in {"RGCN", "RLCN", "RCSN", "RECN", "RNAN", "PNG"}:
            options.extend([
                ("readable", "Readable PNG preview", "Export DSM's direct preview/contact sheet for this asset."),
                ("related_png", "Combined PNG using related tiles/palettes/cells", "Use the asset graph to pair NCGR/NCLR/NSCR/NCER/NANR and compose the best preview DSM can."),
            ])
        elif asset.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}:
            options.extend([
                ("audio_bundle", "Audio bundle: raw + WAV previews", "Best-quality practical output: raw original pieces plus lossless WAV previews where DSM can decode samples/streams."),
                ("audio_bundle_mp3", "Audio bundle + optional MP3", "Also writes high-quality MP3 copies when ffmpeg is installed. WAV remains the quality-first output."),
                ("audio_open", "Create WAV preview and open it", "Exports to a preview folder and opens the first WAV with your OS default player."),
            ])
        else:
            options.append(("readable", "Try readable decode", "Try DSM's readable exporter if this format has a decoder."))
        return options

    def _choose_export_option(self, asset: Asset, options: list[tuple[str, str, str]]) -> str | None:
        return self._choose_export_option_dialog(
            "Export Selected",
            f"Choose how to export:\n{asset.magic} — {asset.virtual_path}",
            options,
        )

    def _choose_export_option_dialog(
        self,
        title: str,
        header: str,
        options: list[tuple[str, str, str]],
    ) -> str | None:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        label = QLabel(header)
        label.setWordWrap(True)
        layout.addWidget(label)
        buttons: list[QRadioButton] = []
        for idx, (_key, option_label, desc) in enumerate(options):
            rb = QRadioButton(f"{option_label}\n  {desc}")
            rb.setChecked(idx == 0)
            layout.addWidget(rb)
            buttons.append(rb)
        box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        box.accepted.connect(dialog.accept)
        box.rejected.connect(dialog.reject)
        layout.addWidget(box)
        if dialog.exec() != QDialog.Accepted:
            return None
        for rb, (key, _label, _desc) in zip(buttons, options):
            if rb.isChecked():
                return key
        return options[0][0] if options else None

    def _run_export_choice(self, asset: Asset, choice: str, out: Path) -> list[Path]:
        written: list[Path] = []
        if choice == "raw":
            self._update_status("Writing raw selected asset...")
            return [export_asset(asset, out, decoded=True)]
        if choice == "readable":
            self._update_status("Running DSM readable decoder...")
            return export_readable_asset(asset, out)
        if choice == "related_png":
            self._update_status("Composing PNG preview from graph-paired related assets...")
            related = self._graph_related_assets(asset, limit=32)
            images = decode_nitro2d_related_preview(asset, related)
            if images:
                return save_preview_images(images, out / f"dsm_related_{asset.asset_id}", prefix=Path(asset.virtual_path).stem)
            return export_readable_asset(asset, out)
        if choice == "texture_apicula":
            tex_out = out / f"dsm_texture_{asset.asset_id}"
            result = convert_texture_with_apicula(asset, tex_out)
            if not result.ok:
                raise RuntimeError(result.message)
            return result.output_files or texture_outputs(tex_out)
        if choice in {"model_glb", "model_dae"}:
            fmt = "glb" if choice == "model_glb" else "dae"
            model_out = out / f"dsm_model_{asset.asset_id}_{fmt}"
            related = self._model_related_assets(asset)
            self._write_graph_manifest(asset, model_out)
            result = convert_with_apicula(asset, model_out, sibling_assets=related, output_format=fmt)
            if not result.ok:
                raise RuntimeError(result.message)
            return result.output_files
        if choice == "model_obj":
            return self._export_model_obj(asset, out)
        if choice == "model_bundle":
            return self._export_model_bundle_to(asset, out)
        if choice in {"audio_bundle", "audio_bundle_mp3", "audio_open"}:
            from .audio import export_audio_bundle
            base = out if choice != "audio_open" else (self.preview_temp / "audio_previews")
            written = export_audio_bundle(asset, base, make_mp3=(choice == "audio_bundle_mp3"))
            if choice == "audio_open":
                wavs = [p for p in written if p.suffix.lower() == ".wav"]
                if wavs:
                    self._open_path(wavs[0])
            return written
        return export_readable_asset(asset, out)

    def _model_related_assets(self, asset: Asset) -> list[Asset]:
        graph_related = self._graph_related_assets(asset, limit=96)
        resolver_related = self._sibling_assets(asset)
        pinned = self._pinned_texture_asset()
        out: list[Asset] = []
        seen = {asset.asset_id}
        for item in ([pinned] if pinned else []) + graph_related + resolver_related:
            if item and item.asset_id not in seen and item.magic in {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}:
                seen.add(item.asset_id)
                out.append(item)
        self._update_status(f"Model export will supply {len(out)} resolved/manual texture and animation sibling(s) to apicula.")
        return out[:96]

    def _export_model_obj(self, asset: Asset, out: Path) -> list[Path]:
        tmp = out / f"dsm_model_{asset.asset_id}_glb_for_obj"
        related = self._model_related_assets(asset)
        result = convert_with_apicula(asset, tmp, sibling_assets=related, output_format="glb")
        if not result.ok or not result.output_files:
            raise RuntimeError(result.message)
        try:
            import trimesh
        except Exception as exc:
            raise RuntimeError(f"trimesh is required for OBJ bridge export: {exc}") from exc
        obj_dir = out / f"dsm_model_{asset.asset_id}_obj"
        obj_dir.mkdir(parents=True, exist_ok=True)
        scene = trimesh.load(result.output_files[0], force="scene")
        obj_path = obj_dir / "model.obj"
        scene.export(obj_path)
        self._write_graph_manifest(asset, obj_dir)
        return [obj_path] + list(obj_dir.glob("*.mtl")) + list(obj_dir.glob("*.png"))

    def _export_model_bundle_to(self, asset: Asset, out: Path) -> list[Path]:
        base = out / f"dsm_bundle_{asset.asset_id}"
        raw_dir = base / "raw_nitro"
        glb_dir = base / "converted_glb"
        dae_dir = base / "converted_dae"
        texture_dir = base / "texture_images"
        for d in (raw_dir, glb_dir, dae_dir, texture_dir):
            d.mkdir(parents=True, exist_ok=True)
        related = self._model_related_assets(asset)
        written = [export_asset(asset, raw_dir, decoded=True)]
        written.extend(export_assets(related, raw_dir / "related", decoded=True))
        self._write_graph_manifest(asset, base)
        # Decode resolved/manual textures directly; this is useful even if apicula does not embed them.
        for texture_asset in [r for r in related if r.magic == "BTX0"][:96]:
            try:
                written.extend(export_readable_asset(texture_asset, texture_dir / "dsm_decoded"))
            except Exception as exc:
                self._update_status(f"Texture decode failed for {texture_asset.virtual_path}: {exc}")
        glb = convert_with_apicula(asset, glb_dir, sibling_assets=related, output_format="glb")
        dae = convert_with_apicula(asset, dae_dir, sibling_assets=related, output_format="dae")
        commands = []
        for label, result in (("GLB", glb), ("DAE", dae)):
            commands.append(f"[{label}] ok={result.ok}")
            commands.append(" ".join(str(part) for part in result.command))
            commands.append(result.message)
            commands.append("")
            written.extend(result.output_files)
        log = base / "conversion_commands.txt"
        log.write_text("\n".join(commands), encoding="utf-8")
        written.append(log)
        if not glb.ok and not dae.ok:
            raise RuntimeError(glb.message or dae.message)
        return written

    def _write_graph_manifest(self, asset: Asset, out: Path) -> Path:
        out.mkdir(parents=True, exist_ok=True)
        path = out / "dsm_asset_graph.json"
        path.write_text(json.dumps(graph_to_manifest(self.asset_graph, asset, self.assets_by_id), indent=2), encoding="utf-8")
        return path

    def _open_path(self, path: Path) -> None:
        try:
            if sys.platform.startswith("darwin"):
                subprocess.Popen(["open", str(path)])
            elif os.name == "nt":
                os.startfile(str(path))  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", str(path)])
            self._update_status(f"Opened preview: {path}")
        except Exception as exc:
            self._update_status(f"Could not open preview automatically: {exc}")

    def export_readable_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select an asset first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose readable export folder")
        if not out_dir:
            return
        try:
            written = export_readable_asset(asset, out_dir)
        except Exception as exc:
            QMessageBox.warning(self, "Readable export failed", str(exc))
            self._update_status("Readable export failed.")
            return
        if written:
            QMessageBox.information(self, "Readable export complete", f"Wrote {len(written)} file(s) to:\n{Path(out_dir)}")
            self._update_status(f"Wrote {len(written)} readable file(s) from {asset.virtual_path}")
        else:
            QMessageBox.information(self, "No readable decoder", "DSM does not have a readable PNG export for this asset yet. Use Export Selected to save the raw decoded file.")
            self._update_status("No readable decoder for selected asset.")

    def export_selected(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select an asset first.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not out_dir:
            return
        out_path = export_asset(asset, out_dir, decoded=True)
        self._update_status(f"Exported {asset.virtual_path} -> {out_path}")

    def export_visible(self) -> None:
        if not self.visible_assets:
            QMessageBox.information(self, "Nothing to export", "There are no visible assets to export.")
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not out_dir:
            return
        exported = export_assets(self.visible_assets, out_dir, decoded=True)
        self._update_status(f"Exported {len(exported)} asset(s) to {out_dir}")

    def export_blender_bundle(self) -> None:
        asset = self.selected_asset()
        if not asset:
            QMessageBox.information(self, "No selection", "Select a BMD0 model first.")
            return
        if asset.magic != "BMD0":
            QMessageBox.information(self, "Not a model", "Select a BMD0 model row first.")
            return
        if not apicula_available():
            QMessageBox.warning(self, "apicula not found", apicula_help_text())
            return
        out_dir = QFileDialog.getExistingDirectory(self, "Choose bundle export folder")
        if not out_dir:
            return

        base = Path(out_dir) / f"dsm_bundle_{asset.asset_id}"
        raw_dir = base / "raw_nitro"
        glb_dir = base / "converted_glb"
        dae_dir = base / "converted_dae"
        base.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        siblings = self._sibling_assets(asset)
        export_asset(asset, raw_dir, decoded=True)
        exported_siblings = export_assets(siblings, raw_dir / "related", decoded=True)
        texture_pool = [s for s in siblings if s.magic == "BTX0"]
        report = texture_match_report(asset, texture_pool or self._pokemon_path_texture_candidates(asset, limit=64))
        (base / "texture_match_report.txt").write_text(report, encoding="utf-8")

        glb_result = convert_with_apicula(asset, glb_dir, sibling_assets=siblings, output_format="glb")
        dae_result = convert_with_apicula(asset, dae_dir, sibling_assets=siblings, output_format="dae")

        texture_dir = base / "texture_images"
        dsm_decoded_count = 0
        for texture_asset in texture_pool[:64]:
            try:
                dsm_decoded_count += len(export_readable_asset(texture_asset, texture_dir / "dsm_decoded"))
            except Exception:
                pass

        texture_results = []
        for texture_asset in texture_pool[:24]:
            tex_out = texture_dir / "apicula" / texture_asset.asset_id
            texture_results.append((texture_asset, convert_texture_with_apicula(texture_asset, tex_out)))

        commands = [f"[DSM_DECODED_TEXTURE_PNGS] count={dsm_decoded_count}", ""]
        for label, result in (("GLB", glb_result), ("DAE", dae_result)):
            commands.append(f"[{label}] ok={result.ok}")
            commands.append(" ".join(str(part) for part in result.command))
            commands.append(result.message)
            commands.append("")
        for texture_asset, result in texture_results:
            commands.append(f"[TEXTURE {texture_asset.asset_id}] ok={result.ok} path={texture_asset.virtual_path}")
            commands.append(" ".join(str(part) for part in result.command))
            commands.append(result.message)
            commands.append("")
        (base / "apicula_commands.txt").write_text("\n".join(commands), encoding="utf-8")

        if glb_result.ok or dae_result.ok:
            msg = (
                f"Exported Blender bundle to {base}\n\n"
                f"Raw model + {len(exported_siblings)} related texture/animation file(s) are in raw_nitro/.\n"
                f"Converted GLB/DAE outputs, {dsm_decoded_count} DSM-decoded texture PNG(s), apicula texture fallbacks, and logs are inside the bundle."
            )
            QMessageBox.information(self, "Bundle exported", msg)
            self._update_status(msg)
        else:
            QMessageBox.warning(self, "Bundle conversion failed", glb_result.message or dae_result.message)
            self._update_status("Bundle export wrote raw files, but conversion failed. Check apicula_commands.txt.")

    def _cached_preview_texture_paths(self, out_dir: Path) -> list[Path]:
        tex_dir = out_dir / "dsm_decoded_textures"
        if not tex_dir.exists():
            return []
        return sorted([p for p in tex_dir.rglob("*.png") if p.is_file()])[:32]

    def convert_preview_selected(self, *, manual: bool = True, force: bool = False) -> None:
        asset = self.selected_asset()
        if not asset:
            if manual:
                QMessageBox.information(self, "No selection", "Select a model asset first.")
            return
        self._preview_model_with_textures(asset, manual=manual, force=force, switch_to_details=False)

    def _start_geometry_preview(self, asset: Asset, *, manual: bool) -> None:
        if asset.magic != "BMD0":
            if manual:
                QMessageBox.information(self, "Not a model", "Preview/conversion works best on BMD0 model assets. Select a BMD0 row.")
            else:
                self.preview.show_message("Selected row is not a BMD0 model. Filter for BMD0 to preview models.")
            return
        if not apicula_available():
            if manual:
                QMessageBox.warning(self, "apicula not found", apicula_help_text())
            else:
                self.preview.show_message(apicula_help_text())
                self._update_status("apicula was not found, so DSM can list/export but not preview models yet.")
            return

        out_dir = self.preview_temp / asset.asset_id / "geometry_preview"
        cached = converted_outputs(out_dir)
        if cached:
            preview_path = _best_preview_path(cached) or cached[0]
            self.preview.load_glb(preview_path, fallback_textures=[])
            self._last_previewed_asset_id = asset.asset_id
            self._preview_fallback_count_by_asset_id[asset.asset_id] = 0
            self._preview_status_by_asset_id[asset.asset_id] = self._preview_result_text(preview_path, [])
            self._update_status(f"Previewing geometry-only fallback: {preview_path}")
            self._update_preview_details(asset)
            return

        if self.preview_worker is not None and self.preview_worker.isRunning():
            self._queued_preview_asset_id = asset.asset_id
            self._update_status(f"Queued geometry preview for {asset.virtual_path}...")
            return

        self.preview.show_message(
            f"Preparing geometry-only preview...\n{asset.virtual_path}\n\n"
            "Texture resolution did not produce a textured preview for this model."
        )
        self._update_status(f"Starting geometry-only preview for {asset.virtual_path}...")
        self.preview_worker = PreviewWorker(asset, out_dir, self.assets, self._pinned_texture_asset_id, graph_related_assets=[])
        self.preview_worker.progress.connect(self._update_status)
        self.preview_worker.finished_ok.connect(self._preview_finished)
        self.preview_worker.failed.connect(self._preview_failed)
        self.preview_worker.start()

    def _preview_finished(self, asset_id: str, result: object) -> None:
        asset = self.selected_asset()
        if asset and asset.asset_id == asset_id and getattr(result, "output_files", None):
            first = _best_preview_path(result.output_files) or result.output_files[0]
            self.preview.load_glb(first, fallback_textures=list(getattr(result, "auxiliary_files", [])))
            self._last_previewed_asset_id = asset_id
            fallback_paths = list(getattr(result, "auxiliary_files", []))
            self._preview_fallback_count_by_asset_id[asset_id] = len(fallback_paths)
            status = self._preview_result_text(first, fallback_paths)
            self._preview_status_by_asset_id[asset_id] = status
            quality = _converted_texture_quality(first)
            if quality.confident:
                self._update_status(f"Previewing {first}. Converted outputs are in {first.parent}; {quality.summary()}.")
            elif quality.weak_material_only:
                self._update_status(f"Previewing {first}. Geometry-only preview loaded.")
            else:
                self._update_status(f"Previewing {first}. Geometry-only preview loaded.")
            self._update_preview_details(asset)
        else:
            self._update_status("Conversion finished for a previously selected row.")
        self._finish_texture_preview_queue(asset_id)

    def _preview_failed(self, asset_id: str, message: str) -> None:
        current = self.selected_asset()
        if current and current.asset_id == asset_id:
            self.preview.show_message(f"Conversion failed:\n{message}")
        self._update_status("Conversion failed.")
        self._finish_texture_preview_queue(asset_id)

    def _sibling_assets(self, asset: Asset) -> list[Asset]:
        # Used by manual bundle export.
        result = build_related_assets(
            asset,
            self.assets,
            pinned_texture_asset_id=self._pinned_texture_asset_id,
            progress=self._update_status,
            texture_library=self._texture_library_for_session(),
        )
        return result.assets

    def _texture_matches_for_model(self, asset: Asset, *, limit: int = 16):
        return texture_matches_for_model(asset, self.assets, limit=limit, progress=None)

    def _pokemon_path_texture_candidates(self, asset: Asset, *, limit: int = 24) -> list[Asset]:
        return pokemon_path_texture_candidates(asset, self.assets, limit=limit)

    def _graph_related_assets(self, asset: Asset, *, relation: str | None = None, limit: int = 64) -> list[Asset]:
        if not getattr(self, "asset_graph", None):
            return []
        return self.asset_graph.related_assets(asset, self.assets_by_id, relation=relation, limit=limit)

    def _apply_tree_folder_labels(self, item: QTreeWidgetItem, parts: tuple[str, ...], rows: list[int], *, raw: bool) -> None:
        count = len(rows)
        breadcrumb = " / ".join(parts)
        item.setText(0, parts[-1])
        item.setText(1, "")
        item.setText(2, f"{count} asset{'s' if count != 1 else ''}")
        item.setText(3, breadcrumb)
        item.setToolTip(0, breadcrumb)

    def _asset_display_name(self, asset: Asset) -> str:
        cached = self._display_name_cache.get(asset.asset_id)
        if cached is not None:
            return cached
        label = asset_browser_name(asset)
        self._display_name_cache[asset.asset_id] = label
        return label

    def _asset_file_label(self, asset: Asset) -> str:
        return asset_filename_label(asset.virtual_path)

    def _asset_names(self, asset: Asset) -> set[str]:
        names = self._name_cache.get(asset.asset_id)
        if names is None:
            if asset.size > 8 * 1024 * 1024:
                self._update_status(f"Skipping synchronous name scan for large asset {asset.virtual_path} ({human_size(asset.size)}). Use Set Textures or Export Selected… to run focused matching in a worker.")
                names = set()
            else:
                names = extract_nitro_names(asset.data)
            self._name_cache[asset.asset_id] = names
        return names

    def _load_profile_summary(self) -> None:
        if not self.rom_path:
            self.profile_text = ""
            return
        try:
            rom = NDSRom.from_path(self.rom_path)
            files = list(rom.iter_files())
            profile = detect_profile(rom.info.title, rom.info.game_code, [f.path for f in files])
            mapping = choose_mapping(rom.info.title, rom.info.game_code)
            self.current_mapping = mapping
            parts = [f"Profile: {profile.label} ({profile.confidence}).", mapping_summary(mapping)]
            if profile.priority_queries:
                parts.append("Useful searches: " + ", ".join(profile.priority_queries) + ".")
            if profile.priority_paths:
                parts.append("Priority paths/filters: " + ", ".join(profile.priority_paths) + ".")
            if profile.notes:
                parts.append("Notes:\n" + "\n".join(f"- {note}" for note in profile.notes))
            self.profile_text = "\n".join(parts)
        except Exception:
            self.current_mapping = None
            self.profile_text = ""

    def _update_status(self, text: str) -> None:
        # Keep the status bar short so it never steals browser/terminal space.
        first_line = str(text).splitlines()[0] if text else ""
        if len(first_line) > 140:
            first_line = first_line[:137] + "..."
        self.statusBar().showMessage(first_line, 7000)
        if hasattr(self, "log_box"):
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.log_box.append(f"[{timestamp}] {text}")


def main() -> None:
    # Helps some OpenGL setups behave better with Qt.
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
