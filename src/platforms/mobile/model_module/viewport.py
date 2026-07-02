from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QMessageBox

from ....install import project_root
from ...home.home_textures import align_home_bindings_to_glb, HomeTextureBindings


def texture_payload_from_result(result: dict) -> dict:
    texture_paths = [Path(path) for path in result.get("texture_paths") or [] if str(path).strip()]
    texture_by_name = {
        name: Path(path)
        for name, path in (result.get("texture_by_name") or {}).items()
        if str(path).strip()
    }
    material_to_texture = dict(result.get("material_to_texture") or {})
    glb_path = result.get("glb_path")
    if glb_path and texture_paths:
        bindings = align_home_bindings_to_glb(
            Path(glb_path),
            HomeTextureBindings(
                texture_paths=texture_paths,
                texture_by_name=texture_by_name,
                material_to_texture=material_to_texture,
                texture_bind_order=list(result.get("texture_bind_order") or []),
                fallback_paths=texture_paths,
            ),
            texture_paths=texture_paths,
        )
        material_to_texture = dict(bindings.material_to_texture)
        texture_by_name = dict(bindings.texture_by_name)
    return {
        "fallback_textures": texture_paths,
        "texture_by_name": texture_by_name,
        "material_to_texture": material_to_texture,
        "texture_bind_order": list(result.get("texture_bind_order") or []),
        "texture_sheet_entries": list(result.get("texture_sheet_entries") or []),
    }


def show_preview_in_viewport(window, asset, result: dict) -> bool:
    glb_path = result["glb_path"]
    path = Path(glb_path).expanduser().resolve()
    if not path.is_file():
        return False
    asset_id = str(getattr(asset, "asset_id", "") or "")
    payload = texture_payload_from_result(result)
    sheet_entries = list(payload.get("texture_sheet_entries") or [])
    if hasattr(window, "_load_model_preview_glb"):
        window._load_model_preview_glb(
            path,
            asset_id=asset_id,
            fallback_textures=list(payload["fallback_textures"]),
            texture_by_name=dict(payload["texture_by_name"]),
            material_to_texture=dict(payload["material_to_texture"]),
            texture_bind_order=list(payload["texture_bind_order"]),
        )
        if sheet_entries and hasattr(window, "_set_sheet_preview"):
            window._set_sheet_preview(asset_id, sheet_entries)
        elif hasattr(window, "_clear_sheet_preview"):
            window._clear_sheet_preview()
        if hasattr(window, "_update_preview_inspector_visibility"):
            window._update_preview_inspector_visibility(asset)
        if hasattr(window, "_update_preview_details"):
            window._update_preview_details(asset)
        return True
    preview = getattr(window, "preview", None)
    if preview is not None and hasattr(preview, "load_glb"):
        preview.load_glb(path)
        if hasattr(window, "_update_preview_details"):
            window._update_preview_details(asset)
        return True
    return False


def finish_preview(window, asset, result: dict) -> None:
    glb_path = result["glb_path"]
    source_bundle = result.get("source_bundle", "")
    mesh_name = result.get("mesh_name", "mesh")
    backend = result.get("preview_backend", "")
    texture_payload = texture_payload_from_result(result)
    texture_count = len(texture_payload["fallback_textures"])
    if hasattr(window, "_update_status"):
        status = f"Mobile model preview ready: {Path(glb_path).name} from {Path(source_bundle).name}"
        if backend:
            status += f" ({backend})"
        if texture_count:
            status += f" — {texture_count} texture(s)"
        window._update_status(status)
    shown = show_preview_in_viewport(window, asset, result)
    if shown and hasattr(window, "_preview_fallback_count_by_asset_id"):
        window._preview_fallback_count_by_asset_id[asset.asset_id] = texture_count
    if shown and hasattr(window, "_last_previewed_asset_id"):
        window._last_previewed_asset_id = asset.asset_id
    if not shown:
        if hasattr(window, "preview") and hasattr(window.preview, "show_message"):
            window.preview.show_message(
                f"Mobile preview generated but the viewport could not load it:\n\n{glb_path}\n\n"
                f"Mesh: {mesh_name}\nSource: {source_bundle}"
            )
        QMessageBox.warning(
            window,
            "Viewport preview unavailable",
            f"GLB was exported to:\n{glb_path}\n\nMesh: {mesh_name}\nSource bundle: {source_bundle}",
        )


def default_output_dir() -> Path:
    return project_root() / "exports" / "mobile_model_previews"
