"""Bake preview blobs and color signatures into an EasyFind document."""
from __future__ import annotations

import hashlib
import io
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone

from ..asset_resolver import folder_sibling_assets
from ..platforms.nds.btx0_preview_selection import choose_btx0_thumbnail_texture_name
from ..platforms.nds.nitro.decode import decode_btx_images, decode_guided_tex0_images
from ..platforms.nds.nitro.export import make_contact_sheet
from ..platforms.nds.nitro.types import DecodedImage
from ..platforms.nds.nitro_2d import (
    decode_nitro2d_preview,
    decode_nitro2d_thumbnail_preview,
)
from ..scanner import Asset
from .build_index import EASYFIND_EXCLUDED_NODE_MAGICS
from .build_options import BUILD_MODE_CATCHUP, BUILD_MODE_FULL, BUILD_MODE_TYPES, EasyFindBuildOptions
from .color_buckets import bucket_for_node_kind, dominant_bucket_from_rgba
from .format import PREVIEW_BLOBS_DIR
from ..texture_library import TextureLibrary
from .model_thumbnail import bake_bmd0_thumbnail_bytes
from .preview_policy import preview_skipped_reason, should_bake_preview
from .models import EasyFindColorSignature, EasyFindDocument, EasyFindNode, EasyFindPreviewRef

Progress = Callable[[str], None]

_PREVIEW_MAX = 128
_NITRO_2D_MAGICS = frozenset({"RGCN", "RCSN", "RECN", "RNAN", "NFTR"})
_RELATED_MAGICS = frozenset({"RLCN", "RGCN", "RCSN", "RECN", "RNAN"})


def _thumbnail_pil(im):
    from PIL import Image

    if max(im.width, im.height) <= _PREVIEW_MAX:
        return im
    copy = im.copy()
    copy.thumbnail((_PREVIEW_MAX, _PREVIEW_MAX), Image.Resampling.LANCZOS)
    return copy


def _png_bytes_from_pil_image(im) -> tuple[bytes, int, int]:
    im = _thumbnail_pil(im.convert("RGBA"))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue(), im.width, im.height


def _png_bytes_from_decoded(image: DecodedImage) -> tuple[bytes, int, int]:
    return _png_bytes_from_pil_image(image.to_pil())


def _preview_pil_from_decoded_images(images: list[DecodedImage]):
    if not images:
        return None
    if len(images) > 1:
        return make_contact_sheet(images, max_thumb=_PREVIEW_MAX, columns=3)
    return images[0].to_pil()


def _asset_for_node(node: EasyFindNode, by_id: dict[str, Asset]) -> Asset | None:
    virtual_id = str(node.metadata.get("virtual_asset_id") or "")
    if virtual_id and virtual_id in by_id:
        return by_id[virtual_id]
    if node.asset_refs:
        parent = by_id.get(node.asset_refs[0].asset_id)
        sub_id = node.asset_refs[0].sub_id
        if parent is not None and sub_id and getattr(parent, "is_texture_slot", False):
            return parent
        if parent is not None and sub_id:
            slot_id = f"{parent.asset_id}::slot::{sub_id.replace('/', '_')}"
            return by_id.get(slot_id, parent)
    return None


def _related_assets(asset: Asset, assets_by_id: dict[str, Asset]) -> list[Asset]:
    return folder_sibling_assets(
        asset,
        assets_by_id.values(),
        allowed_magics=_RELATED_MAGICS,
        limit=24,
    )


def _bmd0_texture_fallback_thumbnail(
    asset: Asset,
    all_assets: list[Asset],
) -> tuple[bytes | None, int | None, int | None]:
    """When GLB bake fails, use a colocated BTX0 slot thumbnail so models still appear."""
    siblings = folder_sibling_assets(
        asset,
        all_assets,
        allowed_magics={"BTX0"},
        limit=8,
    )
    for sibling in siblings:
        texture_name = choose_btx0_thumbnail_texture_name(sibling)
        if texture_name:
            images = decode_guided_tex0_images(
                sibling.data,
                texture_requests=[(texture_name, None)],
                max_images=1,
            )
            if images:
                return _png_bytes_from_decoded(images[0])
        images = decode_btx_images(sibling.data, max_images=1, mode="resolved")
        if images:
            return _png_bytes_from_decoded(images[0])
    return None, None, None


def _png_bytes_from_asset(
    asset: Asset,
    all_assets: list[Asset],
    *,
    texture_library: TextureLibrary | None = None,
    progress: Progress | None = None,
    web_snapshot=None,
) -> tuple[bytes | None, int | None, int | None]:
    upper = asset.magic.upper()
    if upper in EASYFIND_EXCLUDED_NODE_MAGICS:
        return None, None, None

    data = asset.data
    if upper == "PNG" and data[:8] == b"\x89PNG\r\n\x1a\n":
        try:
            from PIL import Image
        except Exception:
            return data, None, None
        return _png_bytes_from_pil_image(Image.open(io.BytesIO(data)))

    # BMD0: render an orthographic GLB snapshot for the EasyFind canvas.
    if upper == "BMD0":
        baked = bake_bmd0_thumbnail_bytes(
            asset,
            all_assets,
            texture_library=texture_library,
            progress=progress,
            web_snapshot=web_snapshot,
        )
        if baked[0]:
            return baked
        # Never substitute a random BTX0 texture — that mislabels models on the map.
        if web_snapshot is not None:
            return None, None, None
        return _bmd0_texture_fallback_thumbnail(asset, all_assets)

    if upper == "BTX0":
        texture_name = choose_btx0_thumbnail_texture_name(asset)
        if texture_name:
            images = decode_guided_tex0_images(
                data,
                texture_requests=[(texture_name, None)],
                max_images=1,
            )
            if images:
                return _png_bytes_from_decoded(images[0])
        images = decode_btx_images(data, max_images=1, mode="resolved")
        if not images:
            images = decode_btx_images(data, max_images=1, mode="all-palettes")
        if images:
            return _png_bytes_from_decoded(images[0])
        return None, None, None

    if upper in _NITRO_2D_MAGICS:
        related = _related_assets(asset, {a.asset_id: a for a in all_assets})
        thumb = decode_nitro2d_thumbnail_preview(asset, related)
        if thumb is None:
            solo = decode_nitro2d_preview(data, upper)
            thumb = solo[0] if solo else None
        if thumb is not None:
            return _png_bytes_from_decoded(thumb)
        return None, None, None

    decoded = decode_nitro2d_preview(data, upper)
    if decoded:
        return _png_bytes_from_decoded(decoded[0])
    return None, None, None


def _signature_from_png(png_bytes: bytes, node_id: str) -> EasyFindColorSignature:
    try:
        from PIL import Image
    except Exception:
        return _neutral_signature(node_id, "unknown")

    im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    rgba = im.tobytes()
    dominant, colors, secondary_buckets, brightness, saturation, transparent = dominant_bucket_from_rgba(
        rgba, im.width, im.height,
    )
    return EasyFindColorSignature(
        signature_id=f"sig:{node_id}",
        node_id=node_id,
        dominant_bucket=dominant,
        dominant_colors=colors,
        secondary_buckets=secondary_buckets,
        brightness=brightness,
        saturation=saturation,
        transparent_ratio=transparent,
        metadata={},
    )


def _neutral_signature(
    node_id: str,
    bucket: str,
    *,
    metadata: dict[str, object] | None = None,
) -> EasyFindColorSignature:
    return EasyFindColorSignature(
        signature_id=f"sig:{node_id}",
        node_id=node_id,
        dominant_bucket=bucket,
        dominant_colors=[],
        secondary_buckets=[],
        brightness="mid",
        saturation="low",
        transparent_ratio=None,
        metadata=dict(metadata or {"synthetic": True}),
    )


def _should_attempt_bake(
    node: EasyFindNode,
    asset: Asset | None,
    *,
    options: EasyFindBuildOptions,
    has_preview: bool,
) -> bool:
    if asset is None or not asset.data or not should_bake_preview(node, asset):
        return False
    if options.mode == BUILD_MODE_CATCHUP:
        return not has_preview
    if options.mode == BUILD_MODE_TYPES:
        return node.node_kind in options.bake_node_kinds
    if options.bake_node_kinds:
        return node.node_kind in options.bake_node_kinds
    return True


def enrich_document_with_previews(
    document: EasyFindDocument,
    assets: list[Asset],
    *,
    options: EasyFindBuildOptions | None = None,
    existing_preview_blobs: dict[str, bytes] | None = None,
    progress: Progress | None = None,
    web_snapshot=None,
) -> tuple[EasyFindDocument, dict[str, bytes]]:
    """Attach preview refs and color signatures; return updated doc and blob map."""
    opts = options or EasyFindBuildOptions(mode=BUILD_MODE_FULL)
    by_id = {asset.asset_id: asset for asset in assets}
    preview_blobs: dict[str, bytes] = dict(existing_preview_blobs or {})
    preview_refs_by_node = {ref.node_id: ref for ref in document.preview_refs}
    signatures_by_node = {sig.node_id: sig for sig in document.color_signatures}
    updated_nodes: list[EasyFindNode] = []
    total = len(document.nodes)
    baked_count = 0
    texture_library: TextureLibrary | None = None
    needs_model_textures = any(
        _should_attempt_bake(node, _asset_for_node(node, by_id), options=opts, has_preview=bool(node.preview_ref))
        and (_asset_for_node(node, by_id) is not None and _asset_for_node(node, by_id).magic.upper() == "BMD0")
        for node in document.nodes
    )

    for index, node in enumerate(document.nodes):
        if progress and index % 50 == 0:
            progress(f"Baking previews… {index:,}/{total:,}")

        asset = _asset_for_node(node, by_id)
        preview_id = f"prev:{node.node_id}"
        preview_ref_id = node.preview_ref
        signature: EasyFindColorSignature | None = signatures_by_node.get(node.node_id)
        skip_reason = preview_skipped_reason(node, asset)
        has_preview = bool(preview_ref_id)

        if _should_attempt_bake(node, asset, options=opts, has_preview=has_preview):
            is_bmd0 = asset is not None and asset.magic.upper() == "BMD0"
            if needs_model_textures and texture_library is None and is_bmd0:
                if progress:
                    progress("Building ROM texture dictionary for model thumbnails…")
                texture_library = TextureLibrary.from_assets(assets, progress=progress)
            png_bytes, pw, ph = _png_bytes_from_asset(
                asset,
                assets,
                texture_library=texture_library,
                progress=progress,
                web_snapshot=web_snapshot,
            )
            if png_bytes:
                blob_path = f"{PREVIEW_BLOBS_DIR}/{preview_id}.png"
                preview_blobs[preview_id] = png_bytes
                preview_blobs[blob_path] = png_bytes
                preview_refs_by_node[node.node_id] = EasyFindPreviewRef(
                    preview_id=preview_id,
                    node_id=node.node_id,
                    kind="thumbnail",
                    mime_type="image/png",
                    blob_path=blob_path,
                    sha256=hashlib.sha256(png_bytes).hexdigest(),
                    width=pw,
                    height=ph,
                    byte_size=len(png_bytes),
                )
                preview_ref_id = preview_id
                signature = _signature_from_png(png_bytes, node.node_id)
                baked_count += 1
            elif opts.mode != BUILD_MODE_CATCHUP:
                bucket = bucket_for_node_kind(node.node_kind, asset.magic)
                if node.node_kind == "audio":
                    bucket = "audio"
                signature = _neutral_signature(node.node_id, bucket)
        elif signature is None:
            if skip_reason:
                bucket = bucket_for_node_kind(node.node_kind, asset.magic if asset else "")
                if node.node_kind == "audio":
                    bucket = "audio"
                signature = _neutral_signature(node.node_id, bucket, metadata={"preview_skip": skip_reason})
            elif asset is not None and should_bake_preview(node, asset):
                bucket = bucket_for_node_kind(node.node_kind, asset.magic)
                signature = _neutral_signature(
                    node.node_id,
                    bucket,
                    metadata={"preview_not_baked_this_run": True},
                )
            else:
                signature = _neutral_signature(node.node_id, "unknown")

        if signature is None:
            signature = _neutral_signature(node.node_id, "unknown")

        signatures_by_node[node.node_id] = signature
        updated_nodes.append(replace(
            node,
            preview_ref=preview_ref_id,
            color_signature_ref=signature.signature_id,
        ))

    if progress:
        progress(f"Baking previews… {total:,}/{total:,}")

    document.nodes = updated_nodes
    document.preview_refs = sorted(preview_refs_by_node.values(), key=lambda ref: ref.preview_id)
    document.color_signatures = sorted(
        signatures_by_node.values(),
        key=lambda sig: (sig.node_id, sig.signature_id),
    )
    now_utc = datetime.now(timezone.utc).isoformat()
    document.build_info = dict(document.build_info)
    document.build_info["build_mode"] = "index+previews"
    document.build_info["preview_count"] = len(document.preview_refs)
    document.build_info["color_signature_count"] = len(document.color_signatures)
    document.build_info["last_build_mode"] = opts.mode
    document.build_info["last_baked_count"] = baked_count
    if opts.bake_node_kinds:
        document.build_info["last_baked_kinds"] = sorted(opts.bake_node_kinds)
    document.build_info["last_build_utc"] = now_utc
    from .node_index import attach_bucket_lookup

    attach_bucket_lookup(document)
    if document.manifest.build:
        document.manifest.build = dict(document.manifest.build)
        document.manifest.build["build_mode"] = "index+previews"
        document.manifest.build["completed_utc"] = now_utc
        document.manifest.build["last_update_mode"] = opts.mode
    document.manifest.updated_utc = now_utc

    return document, preview_blobs
