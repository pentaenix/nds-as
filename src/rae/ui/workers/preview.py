"""Model and image preview background workers."""
from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from ...asset_resolver import MODEL_ANIMATION_MAGICS, folder_sibling_assets
from ...exporter import convert_texture_with_apicula, convert_with_apicula, texture_outputs
from ...glb_preview_textures import merge_texture_by_name, merge_texture_paths, texture_map_from_paths
from ...model_texture_resolver import build_preview_texture_maps, resolve_model_textures, write_resolution_images
from ...nitro_2d import decode_nitro2d_preview, decode_nitro2d_related_preview
from ...nitro_textures import decode_btx_images, decode_guided_tex0_images, make_contact_sheet
from ...scanner import Asset
from ...texture_library import TextureLibrary, TextureLibraryStore
from ..preview_quality import TextureQuality, best_preview_path, converted_texture_quality

class PreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(str, object)
    failed = Signal(str, str)

    def __init__(self, asset: Asset, out_dir: Path, all_assets: list[Asset], pinned_texture_asset_id: str | None = None):
        super().__init__()
        self.asset = asset
        self.out_dir = out_dir
        self.all_assets = list(all_assets)
        # Kept for geometry-only fallback when textured preview fails. Normal model
        # preview runs texture resolution automatically via TextureResolveWorker.
        self.pinned_texture_asset_id = pinned_texture_asset_id

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
        *,
        texture_library: TextureLibrary | None = None,
        texture_store: TextureLibraryStore | None = None,
    ):
        super().__init__()
        self.asset = asset
        self.out_dir = out_dir
        self.all_assets = list(all_assets)
        self.pinned_texture_asset_id = pinned_texture_asset_id
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
                self.progress.emit("Set Textures: no exact verified texture binding found. RAE will not pin a fuzzy candidate.")

            for item in folder_sibling_assets(
                self.asset,
                self.all_assets,
                allowed_magics=MODEL_ANIMATION_MAGICS,
                limit=16,
            ):
                if item.asset_id != self.asset.asset_id:
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
                best_path = best_preview_path(result.output_files)
                if best_path is not None:
                    result.output_files = [best_path, *[p for p in result.output_files if p != best_path]]
                apicula_pngs = texture_outputs(trial_dir)
                aux = write_resolution_images(resolution, trial_dir / "dsm_resolved_textures")
                all_pngs = merge_texture_paths(apicula_pngs, aux)
                if all_pngs:
                    result.auxiliary_files = all_pngs
                    resolver_map, material_to_texture, bind_order = build_preview_texture_maps(resolution, aux)
                    apicula_map = texture_map_from_paths(apicula_pngs)
                    result.texture_by_name = merge_texture_by_name(resolver_map, apicula_map)
                    result.material_to_texture = material_to_texture
                    result.texture_bind_order = bind_order
                    report_lines.append(
                        f"RAE preview textures: {len(apicula_pngs)} apicula PNG(s), "
                        f"{len(aux)} resolver PNG(s)"
                    )
                quality = converted_texture_quality(result.output_files[0]) if result.output_files else TextureQuality(0,0,0,0,0,0,0,0)
                report_lines.append(f"Converted preview: {result.output_files[0].name} — {quality.summary()}")
                if not quality.confident and all_pngs:
                    report_lines.append(
                        "Note: apicula GLB references external PNG URIs; RAE loads colocated PNGs "
                        "for preview baking."
                    )
                self.finished_ok.emit(self.asset.asset_id, result, selected_texture_id, "\n".join(report_lines))
                return

            # If apicula fails, still show the resolver report and decoded texture PNGs.
            report_lines.append("apicula conversion failed or produced no mesh-bearing output.")
            report_lines.append(result.message if result else "No conversion result.")
            self.failed.emit(self.asset.asset_id, "\n".join(report_lines))
        except Exception as exc:
            self.failed.emit(self.asset.asset_id, str(exc))


class ImagePreviewWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(int, str, str, str)
    failed = Signal(int, str, str)

    def __init__(
        self,
        asset: Asset,
        out_path: Path,
        label: str,
        *,
        request_id: int,
        related_assets: list[Asset] | None = None,
        texture_name: str | None = None,
    ):
        super().__init__()
        self.asset = asset
        self.out_path = out_path
        self.label = label
        self.request_id = request_id
        self.related_assets = related_assets or []
        self.texture_name = texture_name

    def _decode_btx0_texture_entry(self, texture_name: str) -> list:
        images = decode_guided_tex0_images(
            self.asset.data,
            texture_requests=[(texture_name, None)],
            max_images=8,
        )
        if images:
            return images
        return [
            image
            for image in decode_btx_images(self.asset.data, max_images=32, mode="all-palettes")
            if image.name == texture_name or image.name.startswith(f"{texture_name}__")
        ]

    def run(self) -> None:
        try:
            self.progress.emit(f"Decoding preview images from {self.asset.virtual_path}...")
            if self.asset.magic == "BTX0" and self.texture_name:
                self.progress.emit(f"Decoding BTX0 texture entry '{self.texture_name}'...")
                images = self._decode_btx0_texture_entry(self.texture_name)
            elif self.asset.magic == "BTX0":
                images = decode_btx_images(self.asset.data, max_images=96, mode="all-palettes")
            elif self.related_assets:
                self.progress.emit(f"Composing preview with {len(self.related_assets)} related asset(s)...")
                images = decode_nitro2d_related_preview(self.asset, self.related_assets)
            else:
                images = decode_nitro2d_preview(self.asset.data, self.asset.magic)
            if not images:
                self.failed.emit(self.request_id, self.asset.asset_id, "No readable preview images were decoded from this asset yet.")
                return
            if self.texture_name:
                preview_image = images[0]
                sheet = preview_image.to_pil()
                caption = (
                    f"{self.texture_name} — {preview_image.width}x{preview_image.height}"
                    f"{f' (palette {preview_image.palette_name})' if preview_image.palette_name else ''}"
                    f" from {Path(self.asset.virtual_path).name}"
                )
            else:
                sheet = make_contact_sheet(images, columns=4) if len(images) > 1 else images[0].to_pil()
                names = ", ".join(img.name for img in images[:8])
                if len(images) > 8:
                    names += ", ..."
                caption = f"{self.label}: {len(images)} decoded image(s). {names}\n{self.asset.virtual_path}"
            if sheet is None:
                self.failed.emit(self.request_id, self.asset.asset_id, "No preview sheet could be created.")
                return
            self.out_path.parent.mkdir(parents=True, exist_ok=True)
            sheet.save(self.out_path)
            self.finished_ok.emit(self.request_id, self.asset.asset_id, str(self.out_path), caption)
        except Exception as exc:
            self.failed.emit(self.request_id, self.asset.asset_id, str(exc))


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
