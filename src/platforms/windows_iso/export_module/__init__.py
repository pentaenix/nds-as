from __future__ import annotations

import json
from pathlib import Path

from ....core.modules.protocols import ExportModule
from ....core.assets import Asset
from ..service import prepare_model
from ..submission import export_submission, submission_camera, submission_output_directory


class WindowsIsoExportModule:
    platform_id = "windows_iso"

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            (
                "folder_glb",
                "GLB: decoded models",
                "Write self-contained GLB previews for SMO and animated AM1 rows.",
            ),
        ]

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        if asset.magic in {"WSMO", "WAM1"}:
            return [
                (
                    "models_resource",
                    "DAE submission package + previews",
                    "Write DAE+PNG ZIP, transparent icon, animated-ready GLB, and 750x650 preview.",
                ),
                ("model_glb", "Self-contained GLB", "Write a textured GLB preview model."),
                ("model_dae", "DAE + PNG textures", "Write COLLADA and its PNG textures."),
            ]
        return []

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return False

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        if mode != "folder_glb" or asset.magic not in {"WSMO", "WAM1"}:
            return 0
        descriptor = self._descriptor(asset)
        result = prepare_model(
            descriptor,
            Path(staging) / asset.asset_id,
            include_dae=False,
            progress=self._progress(host),
        )
        return int(result.glb_path.is_file())

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        descriptor = self._descriptor(asset)
        progress = self._progress(host)
        base = Path(out) / asset.asset_id
        if choice == "models_resource":
            preview = getattr(host, "preview", None)
            web_view = getattr(preview, "_web_view", None)
            if web_view is None or not getattr(web_view, "is_available", lambda: False)():
                raise RuntimeError("The Three.js/WebEngine previewer is required for submission icons")
            web_view.set_preview_platform("windows_iso")
            yaw, pitch, zoom = submission_camera(str(descriptor["model"]["path"]))

            def snapshot(path: Path) -> bytes | None:
                return web_view.capture_snapshot_png(
                    path,
                    max(1, int(web_view.width())),
                    max(1, int(web_view.height())),
                    yaw_deg=yaw,
                    pitch_deg=pitch,
                    zoom_factor=zoom,
                )

            target = submission_output_directory(Path(out), descriptor)
            return export_submission(descriptor, target, snapshot, progress=progress)
        result = prepare_model(
            descriptor,
            base,
            include_dae=choice == "model_dae",
            progress=progress,
        )
        if choice == "model_glb":
            return [result.glb_path]
        if choice == "model_dae":
            return [
                *([result.dae_path] if result.dae_path else []),
                *[path for path in result.texture_pngs if path],
            ]
        raise ValueError(f"unsupported Windows ISO export choice: {choice}")

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        if asset.magic not in {"WSMO", "WAM1"}:
            return False, "Select a Marine Park Empire model row first."
        result = prepare_model(
            self._descriptor(asset),
            Path(out_dir) / asset.asset_id,
            include_dae=True,
            progress=self._progress(host),
        )
        return True, f"Exported GLB and DAE to {result.glb_path.parent}"

    @staticmethod
    def _descriptor(asset: Asset) -> dict:
        try:
            value = json.loads(asset.data.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("Windows ISO model descriptor is invalid") from exc
        if not isinstance(value, dict) or value.get("type") != "model":
            raise ValueError("Select a Windows ISO model row")
        return value

    @staticmethod
    def _progress(host: object):
        callback = getattr(host, "_update_status", None)
        return callback if callable(callback) else None
