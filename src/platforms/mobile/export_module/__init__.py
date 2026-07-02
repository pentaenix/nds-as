from __future__ import annotations

from pathlib import Path

from ....core.assets import Asset
from ....core.modules.protocols import ExportModule
from ..rom import export_mobile_asset, export_mobile_readable

_HOME_MODEL_MAGICS = frozenset({"HOME", "ABA", "UNITY"})


class MobileExportModule:
    platform_id = "mobile"

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            ("folder_raw", "ZIP: Raw mobile assets", "Write extracted mobile ROM payloads."),
            ("folder_readable", "ZIP: Readable decodes", "Decode assets when RAE has a mobile decoder."),
        ]

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        options = [
            ("raw", "Original / raw asset", "Save the selected mobile ROM asset as extracted."),
            ("readable", "Try readable decode", "Export decoded output when RAE has a decoder for this asset."),
        ]
        if getattr(asset, "magic", "") == "HOMEUI":
            options.insert(
                0,
                (
                    "home_ui_assets",
                    "HOME app UI assets (icons, sprites, fonts, audio)",
                    "Extract every readable UI texture, sprite, font, and audio clip from base.apk.",
                ),
            )
        if getattr(asset, "magic", "") in _HOME_MODEL_MAGICS:
            options.insert(
                0,
                (
                    "home_model",
                    "HOME model (GLB + shiny + animations)",
                    "Export a portable GLB with embedded textures, a shiny GLB when available, "
                    "and an animation FBX from the HOME Cache.",
                ),
            )
        return options

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return False

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        count = 0
        if mode in {"folder_raw", "folder_mixed"}:
            export_mobile_asset(asset, staging / "raw")
            count += 1
        if mode in {"folder_readable", "folder_mixed"}:
            count += len(export_mobile_readable(asset, staging / "readable" / asset.asset_id))
        return count

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        if choice == "home_model":
            return self._export_home_model(asset, out)
        if choice == "home_ui_assets":
            return self._export_home_ui_assets(asset, out)
        if choice == "raw":
            return [export_mobile_asset(asset, out)]
        return export_mobile_readable(asset, out)

    def _export_home_ui_assets(self, asset: Asset, out: Path) -> list[Path]:
        from ...home.aba_preview import _mobile_rom_root
        from ...home.apk_assets import export_home_ui_assets

        mobile_root = _mobile_rom_root(asset)
        if mobile_root is None:
            raise RuntimeError("Could not locate the mobile ROM root for this HOME UI assets row.")
        out_dir = out if out.is_dir() or out.suffix == "" else out.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        return export_home_ui_assets(mobile_root, out_dir)

    def _export_home_model(self, asset: Asset, out: Path) -> list[Path]:
        from ...home.aba_preview import _mobile_rom_root
        from ...home.assetstudio_preview import home_cache_directories
        from ...home.ids import parse_home_asset_id
        from ...home.model_export import export_home_model_package

        parsed = parse_home_asset_id(str(getattr(asset, "virtual_path", "") or "")) or parse_home_asset_id(
            str(getattr(asset, "asset_id", "") or "")
        )
        payload_id = None
        try:
            import json as _json

            raw = getattr(asset, "data", b"")
            payload = _json.loads(raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw))
            payload_id = payload.get("id") or payload.get("name")
        except Exception:
            payload = {}
        if parsed is None and payload_id:
            parsed = parse_home_asset_id(str(payload_id))
        if parsed is None:
            raise RuntimeError(
                "Could not identify a Pokémon species id for this asset. Select a HOME species "
                "package row (e.g. Psyduck pm0054_00_00) and try again."
            )

        mobile_root = _mobile_rom_root(asset)
        cache_dirs = home_cache_directories(mobile_root) if mobile_root is not None else []
        out_dir = out if out.is_dir() or out.suffix == "" else out.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        result = export_home_model_package(cache_dirs, parsed, out_dir)
        produced = [Path(result.glb_path)]
        if result.shiny_glb_path:
            produced.append(Path(result.shiny_glb_path))
        if result.animation_fbx_path:
            produced.append(Path(result.animation_fbx_path))
        return produced

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        return False, "Blender bundle export is not available for mobile ROM assets."


def build_mobile_export_module() -> ExportModule:
    return MobileExportModule()
