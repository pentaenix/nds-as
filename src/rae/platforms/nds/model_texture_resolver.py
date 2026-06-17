from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from .scanner import Asset
from .nitro_models import MaterialBinding, NsbmdManifest, parse_nsbmd_manifest
from .nitro_textures import (
    DecodedImage,
    format_decode_failure,
    decode_btx_images,
    decode_guided_tex0_images,
    decode_guided_tex0_report,
)
from .texture_library import TextureBinding, TextureLibrary
from ...preview_policy import ModelPreviewPolicy, model_preview_policy

ResolutionStatus = Literal[
    "textured_verified",
    "embedded_texture",
    "exact_match_unverified",
    "manual_override",
    "unresolved",
    "debug_candidate_only",
]


@dataclass(slots=True)
class ResolvedMaterialTexture:
    material_name: str
    texture_name: str
    palette_name: str | None
    texture_asset_id: str | None
    texture_asset_path: str | None
    decoded_image: DecodedImage | None
    reason: str


@dataclass(slots=True)
class ModelTextureResolution:
    status: ResolutionStatus
    model_manifest: NsbmdManifest | None
    bindings: list[ResolvedMaterialTexture] = field(default_factory=list)
    decoded_images: list[DecodedImage] = field(default_factory=list)
    unresolved_materials: list[MaterialBinding] = field(default_factory=list)
    resolved_assets: list[Asset] = field(default_factory=list)
    report: str = ""

    @property
    def verified(self) -> bool:
        return self.status in {"textured_verified", "embedded_texture", "manual_override"} and bool(self.decoded_images)


def _material_texture_requests(manifest: NsbmdManifest) -> list[tuple[str, str | None]]:
    seen: set[tuple[str, str]] = set()
    requests: list[tuple[str, str | None]] = []
    embedded_names = {t.name.casefold() for t in manifest.embedded_tex0.textures} if manifest.embedded_tex0 else set()
    for material in manifest.materials:
        tex_name = material.texture_name or material.material_name
        if not tex_name:
            continue
        if embedded_names and tex_name.casefold() not in embedded_names and material.texture_name is None:
            continue
        key = (tex_name.casefold(), (material.palette_name or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        requests.append((tex_name, material.palette_name))
    if not requests and manifest.embedded_tex0:
        for tex in manifest.embedded_tex0.textures:
            key = (tex.name.casefold(), "")
            if key in seen:
                continue
            seen.add(key)
            requests.append((tex.name, None))
    return requests


def _sibling_btx0_assets(model: Asset, assets: Iterable[Asset]) -> list[Asset]:
    siblings: list[Asset] = []
    seen: set[str] = set()
    for candidate in assets:
        if candidate.magic != "BTX0" or candidate.asset_id in seen:
            continue
        if candidate.folder_key and candidate.folder_key == model.folder_key:
            seen.add(candidate.asset_id)
            siblings.append(candidate)
            continue
        if model.container_chain and candidate.container_chain and candidate.container_chain[: len(model.container_chain)] == model.container_chain:
            seen.add(candidate.asset_id)
            siblings.append(candidate)
    return siblings


def _embedded_resolution(
    model: Asset,
    manifest: NsbmdManifest,
    lines: list[str],
    *,
    policy: ModelPreviewPolicy | None = None,
) -> ModelTextureResolution | None:
    policy = model_preview_policy(policy)
    if policy.geometry_only:
        lines.append(f"- preview quality {policy.label}: embedded texture decode skipped")
        return None
    max_images = policy.max_decoded_images or 128
    if policy.exact_materials_only and manifest.embedded_tex0 is not None:
        guided_report = decode_guided_tex0_report(
            model.data,
            texture_requests=_material_texture_requests(manifest),
            max_images=max_images,
        )
        embedded_images = guided_report.images
        embedded_reason = "embedded TEX0 decoded via exact NSBMD material/dictionary requests"
        if not embedded_images:
            lines.append(
                f"- preview quality {policy.label}: no exact embedded material texture match decoded; broad embedded fallback skipped"
            )
            return None
        lines.append(
            f"- preview quality {policy.label}: exact embedded material decode returned {len(embedded_images)} image(s)"
        )
    else:
        embedded_images = decode_btx_images(model.data, max_images=max_images, mode="resolved")
        embedded_reason = "embedded TEX0 in NSBMD"
        if not embedded_images and manifest.embedded_tex0 is not None:
            embedded_images = decode_btx_images(model.data, max_images=max_images, mode="all-palettes")
            if embedded_images:
                embedded_reason = "embedded TEX0 in NSBMD; palette pairing was not proven, decoded inspectable variants"
                lines.append("- embedded TEX0 strict material/palette pairing did not decode; using embedded palette variants for preview/export")
        if not embedded_images and manifest.embedded_tex0 is not None:
            guided_report = decode_guided_tex0_report(
                model.data,
                texture_requests=_material_texture_requests(manifest),
                max_images=max_images,
            )
            if guided_report.images:
                embedded_images = guided_report.images
                embedded_reason = "embedded TEX0 decoded via NSBMD material/dictionary requests"
                lines.append("- generic embedded decode failed; material-guided TEX0 decode succeeded")
            elif guided_report.failures:
                lines.append("- embedded TEX0 guided decode diagnostics:")
                for failure in guided_report.failures[:12]:
                    lines.extend(f"- {line}" for line in format_decode_failure(failure, source_path=model.virtual_path))
                for summary in guided_report.candidate_summaries[:4]:
                    lines.append(f"- {summary}")
    if not embedded_images:
        return None
    lines.append(f"- embedded TEX0 decoded {len(embedded_images)} texture image(s)")
    if manifest.embedded_tex0 is not None:
        lines.append("- embedded texture dictionary: " + ", ".join(t.name for t in manifest.embedded_tex0.textures[:32]) + (" ..." if len(manifest.embedded_tex0.textures) > 32 else ""))
        lines.append("- embedded palette dictionary: " + (", ".join(p.name for p in manifest.embedded_tex0.palettes[:32]) or "none") + (" ..." if len(manifest.embedded_tex0.palettes) > 32 else ""))
    bindings = [
        ResolvedMaterialTexture(img.name, img.name, img.palette_name, model.asset_id, model.virtual_path, img, embedded_reason)
        for img in embedded_images
    ]
    return ModelTextureResolution("embedded_texture", manifest, bindings, embedded_images, [], [model], "\n".join(lines))


def _resolve_from_library(
    model: Asset,
    manifest: NsbmdManifest,
    texture_library: TextureLibrary,
    *,
    asset_filter: set[str] | None = None,
    reason_prefix: str = "exact NSBMD material texture name matched NSBTX texture dictionary name",
    policy: ModelPreviewPolicy | None = None,
) -> tuple[list[ResolvedMaterialTexture], list[DecodedImage], list[MaterialBinding], list[Asset], list[str]]:
    policy = model_preview_policy(policy)
    max_images = policy.max_decoded_images
    resolved: list[ResolvedMaterialTexture] = []
    decoded_images: list[DecodedImage] = []
    unresolved: list[MaterialBinding] = []
    resolved_assets: list[Asset] = []
    lines: list[str] = []
    seen_images: set[tuple[str, str, str | None]] = set()
    seen_asset_ids: set[str] = set()

    for material in manifest.materials:
        tex_name = material.texture_name or material.material_name
        if not tex_name:
            unresolved.append(material)
            continue
        matches = texture_library.find_exact(tex_name, material.palette_name)
        if asset_filter is not None:
            matches = [m for m in matches if m.texture_asset_id in asset_filter]
        if not matches:
            unresolved.append(material)
            continue
        for binding in matches:
            image, _diag_images, diag_lines = texture_library.decode_binding_with_diagnostics(binding)
            images_for_binding: list[DecodedImage] = []
            reason = binding.reason
            status_for_binding = "strict"
            if image is None:
                if not policy.allow_palette_variants:
                    unresolved.append(material)
                    lines.append(
                        f"- preview quality {policy.label}: skipped palette-variant decode for {binding.texture_name} in {binding.texture_asset_path}"
                    )
                    continue
                images_for_binding = texture_library.decode_texture_all_palettes(binding.texture_asset_id, binding.texture_name)
                if images_for_binding:
                    reason = f"{reason_prefix}; palette pairing not proven, decoded all palette variants"
                    status_for_binding = "palette-variant"
                else:
                    unresolved.append(material)
                    if diag_lines:
                        for diag_line in diag_lines:
                            lines.append(diag_line if diag_line.startswith("  ") else f"- {diag_line}")
                    else:
                        lines.append(f"- exact name {tex_name} found in {binding.texture_asset_path}, but no palette/image could be decoded")
                    continue
            else:
                images_for_binding = [image]

            tex_asset = texture_library.assets_by_id.get(binding.texture_asset_id)
            if tex_asset is not None and tex_asset.asset_id not in seen_asset_ids:
                resolved_assets.append(tex_asset)
                seen_asset_ids.add(tex_asset.asset_id)
            for img in images_for_binding:
                key = (binding.texture_asset_id, img.name, img.palette_name)
                if key not in seen_images:
                    decoded_images.append(img)
                    seen_images.add(key)
                    if max_images is not None and len(decoded_images) >= max_images:
                        lines.append(
                            f"- preview quality {policy.label}: stopped texture decode at {max_images} image(s); switch to Full Fidelity for exhaustive fallback"
                        )
                        return resolved, decoded_images, unresolved, resolved_assets, lines
            first_image = images_for_binding[0] if images_for_binding else None
            resolved.append(ResolvedMaterialTexture(
                material_name=material.material_name,
                texture_name=binding.texture_name,
                palette_name=binding.palette_name or (first_image.palette_name if first_image else None),
                texture_asset_id=binding.texture_asset_id,
                texture_asset_path=binding.texture_asset_path,
                decoded_image=first_image,
                reason=reason,
            ))
            if status_for_binding == "palette-variant":
                lines.append(f"- exact texture name {binding.texture_name} found in {binding.texture_asset_path}; decoded palette variants because the palette pair is not proven")

    return resolved, decoded_images, unresolved, resolved_assets, lines


def _sibling_btx0_resolution(
    model: Asset,
    manifest: NsbmdManifest,
    assets: Iterable[Asset],
    lines: list[str],
    *,
    policy: ModelPreviewPolicy | None = None,
) -> ModelTextureResolution | None:
    policy = model_preview_policy(policy)
    siblings = _sibling_btx0_assets(model, assets)
    if not siblings:
        return None
    requests = _material_texture_requests(manifest)
    if not requests:
        return None
    decoded_images: list[DecodedImage] = []
    resolved_assets: list[Asset] = []
    for btx in siblings:
        images = decode_guided_tex0_images(btx.data, texture_requests=requests, max_images=policy.max_decoded_images or 128)
        if not images:
            continue
        decoded_images.extend(images)
        resolved_assets.append(btx)
    if not decoded_images:
        return None
    lines.append(f"- sibling NSBTX in the same archive/folder decoded {len(decoded_images)} texture image(s) from {len(resolved_assets)} archive(s)")
    bindings = [
        ResolvedMaterialTexture(img.name, img.name, img.palette_name, resolved_assets[0].asset_id, resolved_assets[0].virtual_path, img, "sibling NSBTX in same archive/folder")
        for img in decoded_images
    ]
    status: ResolutionStatus = "exact_match_unverified"
    return ModelTextureResolution(status, manifest, bindings, decoded_images, [], resolved_assets, "\n".join(lines))


def resolve_model_textures(
    model: Asset,
    assets: Iterable[Asset],
    *,
    texture_library: TextureLibrary | None = None,
    manual_texture: Asset | None = None,
    defer_library_build: bool = False,
    progress=None,
    policy: ModelPreviewPolicy | None = None,
) -> ModelTextureResolution:
    policy = model_preview_policy(policy)
    def log(text: str) -> None:
        if progress:
            progress(text)

    manifest = parse_nsbmd_manifest(model.data)
    if manifest is None:
        return ModelTextureResolution("unresolved", None, report="Selected asset is not a valid BMD0/NSBMD model.")

    lines = ["Texture resolution report", f"Model: {model.virtual_path}", ""]
    lines.append(f"Preview quality: {policy.label} ({policy.summary()})")
    if policy.geometry_only:
        lines.append("- texture resolution skipped by selected preview quality")
        return ModelTextureResolution("unresolved", manifest, report="\n".join(lines))
    lines.extend(f"- {note}" for note in manifest.parse_notes)
    lines.append(f"- reliable model dictionary names: {len(manifest.raw_names)}")

    embedded = _embedded_resolution(model, manifest, lines, policy=policy)
    if embedded is not None:
        return embedded

    asset_list = list(assets)

    if texture_library is None:
        if defer_library_build:
            if manual_texture is not None and manual_texture.magic == "BTX0":
                images = decode_btx_images(manual_texture.data, max_images=policy.max_decoded_images or 128, mode="all-palettes")
                lines.append(f"- manual BTX0 override: {manual_texture.virtual_path}")
                lines.append(f"- manual override decoded {len(images)} texture image(s); pairing is user-selected, not proven by model manifest")
                bindings = [
                    ResolvedMaterialTexture(img.name, img.name, img.palette_name, manual_texture.asset_id, manual_texture.virtual_path, img, "manual BTX0 override")
                    for img in images
                ]
                status: ResolutionStatus = "manual_override" if images else "unresolved"
                return ModelTextureResolution(status, manifest, bindings, images, list(manifest.materials), [manual_texture] if images else [], "\n".join(lines))
            missing = sorted({(m.texture_name or m.material_name) for m in manifest.materials if (m.texture_name or m.material_name)})
            lines.append("- external texture lookup deferred until the ROM texture dictionary index is ready")
            if missing:
                lines.append("- requested material texture names: " + ", ".join(missing[:80]) + (" ..." if len(missing) > 80 else ""))
            return ModelTextureResolution("unresolved", manifest, [], [], list(manifest.materials), [], "\n".join(lines))
        log("Building texture dictionary index for exact NSBTX lookups...")
        texture_library = TextureLibrary.from_assets(asset_list, progress=progress)

    # 2. Exact texture dictionary matches.
    log("Resolving model material texture names against the NSBTX texture dictionary...")
    resolved, decoded_images, unresolved, resolved_assets, library_lines = _resolve_from_library(model, manifest, texture_library, policy=policy)
    lines.extend(library_lines)

    if resolved and decoded_images:
        lines.append(f"- exact resolver decoded {len(decoded_images)} texture image(s) from {len(resolved_assets)} NSBTX archive(s)")
        for row in resolved[:24]:
            lines.append(f"  {row.material_name} → {row.texture_name}/{row.palette_name or '?'} from {row.texture_asset_path}")
        status = "textured_verified" if all("palette pairing not proven" not in row.reason for row in resolved) else "exact_match_unverified"
        return ModelTextureResolution(status, manifest, resolved, decoded_images, unresolved, resolved_assets, "\n".join(lines))

    # 2b. Same-archive / same-folder NSBTX siblings before giving up globally.
    sibling_ids = {a.asset_id for a in _sibling_btx0_assets(model, asset_list)}
    if sibling_ids:
        sibling_resolved, sibling_images, sibling_unresolved, sibling_assets, sibling_lines = _resolve_from_library(
            model,
            manifest,
            texture_library,
            asset_filter=sibling_ids,
            reason_prefix="exact material name matched NSBTX in the same archive/folder",
            policy=policy,
        )
        lines.extend(sibling_lines)
        if sibling_resolved and sibling_images:
            lines.append(f"- same-archive resolver decoded {len(sibling_images)} texture image(s) from {len(sibling_assets)} sibling NSBTX archive(s)")
            status = "exact_match_unverified"
            return ModelTextureResolution(status, manifest, sibling_resolved, sibling_images, sibling_unresolved, sibling_assets, "\n".join(lines))

    sibling_guided = _sibling_btx0_resolution(model, manifest, asset_list, lines, policy=policy)
    if sibling_guided is not None:
        return sibling_guided

    # 3. Manual override. This is explicit, not automatic proof. Decode all palettes
    # for inspection and preview fallback, but mark the status honestly.
    if manual_texture is not None and manual_texture.magic == "BTX0":
        images = decode_btx_images(manual_texture.data, max_images=policy.max_decoded_images or 128, mode="all-palettes")
        lines.append(f"- manual BTX0 override: {manual_texture.virtual_path}")
        lines.append(f"- manual override decoded {len(images)} texture image(s); pairing is user-selected, not proven by model manifest")
        bindings = [ResolvedMaterialTexture(img.name, img.name, img.palette_name, manual_texture.asset_id, manual_texture.virtual_path, img, "manual BTX0 override") for img in images]
        status = "manual_override" if images else "unresolved"
        return ModelTextureResolution(status, manifest, bindings, images, unresolved, [manual_texture] if images else [], "\n".join(lines))

    missing = sorted({(m.texture_name or m.material_name) for m in unresolved if (m.texture_name or m.material_name)})
    lines.append("- texture unresolved: no exact decoded texture/palette match was found")
    if missing:
        lines.append("- unresolved requested names: " + ", ".join(missing[:80]) + (" ..." if len(missing) > 80 else ""))
    lines.append("- Advanced candidate search is available only as a diagnostic/manual path; RAE will not pin fuzzy matches as real textures.")
    return ModelTextureResolution("unresolved", manifest, [], [], unresolved, [], "\n".join(lines))


def write_resolution_images(
    resolution: ModelTextureResolution,
    out_dir: str | Path,
    *,
    max_images: int | None = None,
) -> list[Path]:
    from .nitro_textures import save_decoded_images
    if not resolution.decoded_images:
        return []
    images = resolution.decoded_images
    if max_images is not None:
        images = images[:max_images]
    return save_decoded_images(images, out_dir, prefix="resolved_texture")


def build_preview_texture_maps(
    resolution: ModelTextureResolution,
    paths: list[Path],
) -> tuple[dict[str, Path], dict[str, str], list[str]]:
    """Map NSBMD material/texture names to decoded PNG paths for GL preview."""
    texture_by_name: dict[str, Path] = {}
    for path, image in zip(paths, resolution.decoded_images):
        tex_key = image.name.casefold()
        texture_by_name[tex_key] = path
        texture_by_name[path.stem.casefold()] = path
        base = image.name.split("__", 1)[0].casefold()
        if base:
            texture_by_name.setdefault(base, path)

    bind_order: list[str] = []
    seen_tex: set[str] = set()
    manifest = resolution.model_manifest
    if manifest is not None:
        for material in manifest.materials:
            tex = (material.texture_name or material.material_name or "").casefold()
            if tex and tex not in seen_tex:
                seen_tex.add(tex)
                bind_order.append(tex)
    if not bind_order:
        for _path, image in zip(paths, resolution.decoded_images):
            tex_key = image.name.casefold()
            base = image.name.split("__", 1)[0].casefold() or tex_key
            if base not in seen_tex:
                seen_tex.add(base)
                bind_order.append(base)

    material_to_texture: dict[str, str] = {}
    if manifest is not None:
        for material in manifest.materials:
            mat = material.material_name.casefold()
            tex = (material.texture_name or material.material_name or "").casefold()
            if mat and tex:
                material_to_texture[mat] = tex
                path = texture_by_name.get(tex)
                if path is not None:
                    texture_by_name.setdefault(mat, path)
    for binding in resolution.bindings:
        mat = (binding.material_name or "").casefold()
        tex = (binding.texture_name or "").casefold()
        if mat and tex:
            material_to_texture[mat] = tex
            path = texture_by_name.get(tex)
            if path is not None:
                texture_by_name.setdefault(mat, path)
    return texture_by_name, material_to_texture, bind_order
