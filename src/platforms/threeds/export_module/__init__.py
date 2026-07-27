from __future__ import annotations

from pathlib import Path
import tempfile

from ....core.assets import Asset
from ..pokemon_bulk_export import (
    PokemonBulkExportResult,
    pokemon_bulk_export_available,
    pokemon_bulk_export_assets,
    run_pokemon_bulk_export,
)
from ..glbz import write_glbz
from ..rom import load_descriptor, read_garc_slot
from ..service import (
    build_model_glb,
    decode_sprite_png,
    export_animation_payloads,
    export_texture_pngs,
)
from ..world_composition import apply_composition, composition_by_id
from .environment import export_attend_environment_catalog


class ThreedsExportModule:
    platform_id = "3ds"

    def folder_export_options(self) -> list[tuple[str, str, str]]:
        return [
            ("folder_raw", "ZIP: Raw assets", "Write raw GARC payloads for every asset in the folder."),
        ]

    def export_options_for(self, asset: Asset) -> list[tuple[str, str, str]]:
        if asset.magic == "GFMD":
            descriptor = load_descriptor(asset) or {}
            compositions = descriptor.get("world_compositions") or []
            if descriptor.get("type") == "world_model" and compositions:
                default_id = str(descriptor.get("composition_id") or "")
                options: list[tuple[str, str, str]] = []
                for item in compositions:
                    composition_id = str(item.get("id") or "")
                    if not composition_id:
                        continue
                    label = str(item.get("label") or composition_id)
                    suffix = " (default)" if composition_id == default_id else ""
                    options.append(
                        (
                            f"glb_world_{composition_id}",
                            f"GLB complete map{suffix}",
                            label + ". Combines centered model layers, textures, and ambient animations.",
                        )
                    )
                    options.append(
                        (
                            f"glbz_world_{composition_id}",
                            f"GLBZ complete map{suffix}",
                            label + ". Lossless compressed complete map for engine/runtime use.",
                        )
                    )
                    options.append(
                        (
                            f"textures_world_{composition_id}",
                            f"Complete map texture PNGs{suffix}",
                            label + ". Decodes textures used by every composed layer.",
                        )
                    )
                options.extend(
                    [
                        ("glb_standalone", "GLB selected layer only", "Export only this archive slot."),
                        ("glbz_standalone", "GLBZ selected layer only", "Lossless compressed export of only this archive slot."),
                        ("textures_standalone", "Selected layer texture PNGs", "Decode only this archive slot's textures."),
                        (
                            "glbz_attend_catalog",
                            "GLBZ Pokemon Attend environment catalog",
                            "Export every named Alola Attend environment and a validation report.",
                        ),
                        ("raw", "Raw model payload", "Undecoded selected GARC payload."),
                    ]
                )
                return options
            if descriptor.get("type") == "world_model":
                return [
                    ("glb", "GLB world model", "Self-contained world model with textures and material motion metadata."),
                    ("glbz", "GLBZ world model", "Lossless compressed world model."),
                    ("textures", "Texture PNGs", "Decode every world texture to PNG."),
                    (
                        "glbz_attend_catalog",
                        "GLBZ Pokemon Attend environment catalog",
                        "Export every named Alola Attend environment and a validation report.",
                    ),
                    ("raw", "Raw model payload", "Undecoded selected GARC payload."),
                ]
            return [
                (
                    "glb",
                    "GLB model",
                    "One species glTF with skeleton, animations, forms/patterns, and embedded normal + shiny texture sets.",
                ),
                (
                    "glbz",
                    "GLBZ model (lossless, smaller)",
                    "The same complete GLB compressed with zstd and verified byte-for-byte when restored.",
                ),
                ("textures", "Texture PNGs (normal + shiny)", "Decode every texture map to PNG."),
                ("animations_raw", "Raw animation packs", "GFMotion payloads for external tools."),
                ("raw", "Raw model package", "Undecoded GARC payload."),
            ]
        if asset.magic == "GFTX":
            return [
                ("textures", "Texture PNGs (normal + shiny)", "Decode every texture map to PNG."),
                ("raw", "Raw texture package", "Undecoded GARC payload."),
            ]
        if asset.magic == "FLIM":
            return [
                ("sprite_png", "Sprite PNG", "Decoded BFLIM sprite."),
                ("raw", "Raw BFLIM", "Undecoded sprite payload."),
            ]
        return [("raw", "Raw descriptor", "JSON descriptor payload.")]

    def requires_apicula_for_folder_mode(self, mode: str) -> bool:
        return False

    def export_folder_asset(self, host: object, asset: Asset, mode: str, staging: Path) -> int:
        descriptor = load_descriptor(asset)
        if not descriptor:
            return 0
        staging.mkdir(parents=True, exist_ok=True)
        written = self._export_raw(asset, descriptor, staging)
        return len(written)

    def run_export_choice(self, host: object, asset: Asset, choice: str, out: Path) -> list[Path]:
        descriptor = load_descriptor(asset)
        if not descriptor:
            return []
        out.mkdir(parents=True, exist_ok=True)
        progress = getattr(host, "_update_status", None)
        if choice == "glbz_attend_catalog":
            return export_attend_environment_catalog(
                descriptor,
                out / "alola_attend_environments",
                progress=progress,
            )
        if (
            choice.startswith("glb_world_")
            or choice.startswith("glbz_world_")
            or choice.startswith("textures_world_")
        ):
            composition_id = choice.split("_world_", 1)[1]
            composition = composition_by_id(composition_id)
            if composition is None:
                return []
            descriptor = apply_composition(descriptor, composition)
            choice = (
                "glbz"
                if choice.startswith("glbz_world_")
                else "glb"
                if choice.startswith("glb_world_")
                else "textures"
            )
        elif choice in {"glb_standalone", "glbz_standalone", "textures_standalone"}:
            descriptor = {
                key: value
                for key, value in descriptor.items()
                if key not in {
                    "composition_id",
                    "composition_label",
                    "composition_slots",
                    "composition_outer_slot",
                }
            }
            choice = (
                "glb"
                if choice == "glb_standalone"
                else "glbz"
                if choice == "glbz_standalone"
                else "textures"
            )
        if choice == "glb":
            shiny = bool(getattr(host, "_export_glb_shiny", False))
            return [
                build_model_glb(
                    descriptor,
                    out,
                    shiny=shiny,
                    progress=progress,
                )
            ]
        if choice == "glbz":
            shiny = bool(getattr(host, "_export_glb_shiny", False))
            with tempfile.TemporaryDirectory(prefix="rae_threeds_glbz_") as temp_dir:
                glb_path = build_model_glb(
                    descriptor,
                    Path(temp_dir),
                    shiny=shiny,
                    progress=progress,
                )
                output_path = out / glb_path.with_suffix(".glbz").name
                if progress:
                    progress(f"3DS: compressing lossless GLBZ {output_path.name}…")
                return [write_glbz(glb_path, output_path)]
        if choice == "textures":
            return export_texture_pngs(descriptor, out, progress=progress)
        if choice == "animations_raw":
            return export_animation_payloads(descriptor, out)
        if choice == "sprite_png":
            path = out / f"{asset.asset_id}.png"
            path.write_bytes(decode_sprite_png(descriptor))
            return [path]
        if choice == "raw":
            return self._export_raw(asset, descriptor, out)
        return []

    def supports_pokemon_bulk_export(
        self,
        rom_path: str | Path,
        assets: list[Asset],
        *,
        product_code: str | None = None,
    ) -> bool:
        return pokemon_bulk_export_available(rom_path, assets, product_code=product_code)

    def pokemon_bulk_export_assets(self, assets: list[Asset]) -> list[Asset]:
        return pokemon_bulk_export_assets(assets)

    def run_pokemon_bulk_export(
        self,
        host: object,
        assets: list[Asset],
        rom_path: str | Path,
        out_dir: Path,
        *,
        shiny: bool = False,
        progress=None,
    ) -> PokemonBulkExportResult:
        report = progress or getattr(host, "_update_status", None)
        return run_pokemon_bulk_export(
            host,
            assets,
            rom_path,
            out_dir,
            shiny=shiny,
            progress=report,
        )

    def export_blender_bundle(self, host: object, asset: Asset, out_dir: Path) -> tuple[bool, str]:
        if asset.magic != "GFMD":
            return False, "Blender bundle export is only available for 3DS model assets."
        descriptor = load_descriptor(asset)
        if not descriptor:
            return False, "Missing 3DS descriptor payload."
        out_dir.mkdir(parents=True, exist_ok=True)
        paths = [
            build_model_glb(descriptor, out_dir),
        ]
        paths.extend(export_texture_pngs(descriptor, out_dir / "textures"))
        return True, f"Wrote {len(paths)} file(s) to {out_dir}"

    def _export_raw(self, asset: Asset, descriptor: dict, out: Path) -> list[Path]:
        written: list[Path] = []
        kind = descriptor.get("type")
        if kind == "sprite":
            payload = read_garc_slot(
                descriptor["rom"], descriptor["garc"], int(descriptor["sprite_index"])
            )
            path = out / f"{asset.asset_id}.bflim"
            path.write_bytes(payload)
            written.append(path)
        elif kind in {"world_model", "texture_bank"}:
            payload = read_garc_slot(descriptor["rom"], descriptor["garc"], int(descriptor["slot"]))
            path = out / f"{asset.asset_id}.bin"
            path.write_bytes(payload)
            written.append(path)
        elif kind in {"model", "textures"}:
            base = int(descriptor["base_slot"])
            slots = [0] if kind == "model" else [1, 2, 3]
            for offset in slots:
                try:
                    payload = read_garc_slot(descriptor["rom"], descriptor["garc"], base + offset)
                except Exception:
                    continue
                path = out / f"{asset.asset_id}_slot{offset}.bin"
                path.write_bytes(payload)
                written.append(path)
        else:
            path = out / f"{asset.asset_id}.json"
            path.write_bytes(asset.data)
            written.append(path)
        return written
