"""EasyFind file save/load/quick-open APIs."""
from __future__ import annotations

import hashlib
import os
import zipfile
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .format import (
    ASSET_TAGS_PATH,
    ASSETS_PATH,
    BUILD_INFO_PATH,
    BUILD_LOG_PATH,
    COLOR_INDEX_PATH,
    EASYFIND_EXTENSION,
    GROUPS_PATH,
    LAYOUT_PATH,
    LOCATIONS_PATH,
    MANIFEST_PATH,
    MANUAL_MERGES_PATH,
    NOTES_PATH,
    NODES_PATH,
    PREVIEW_BLOBS_DIR,
    PREVIEWS_INDEX_PATH,
    QUICK_OPEN_PATH,
    dumps_json,
    dumps_jsonl_line,
    loads_json,
    parse_jsonl,
)
from .models import (
    EasyFindAssetRef,
    EasyFindAssetTag,
    EasyFindColorSignature,
    EasyFindDocument,
    EasyFindLocation,
    EasyFindManualMerge,
    EasyFindNode,
    EasyFindPreviewRef,
    EasyFindQuickOpen,
)
from .validation import EasyFindCorruptError, EasyFindError

Progress = Callable[[str], None]

# Test hook: set to a callable that raises to simulate write failures.
_write_failure_hook: Callable[[], None] | None = None


def _set_write_failure_hook(hook: Callable[[], None] | None) -> None:
    global _write_failure_hook
    _write_failure_hook = hook


def _location_sort_key(loc: EasyFindLocation) -> tuple:
    return (loc.group, loc.order if loc.order is not None else 0, loc.name, loc.location_id)


def _sync_manifest_counts(document: EasyFindDocument) -> None:
    document.manifest.counts = {
        "assets": len(document.assets),
        "nodes": len(document.nodes),
        "groups": len(document.groups),
        "locations": len(document.locations),
        "manual_merges": len(document.manual_merges),
        "preview_blobs": len(document.preview_refs),
        "color_signatures": len(document.color_signatures),
    }
    document.manifest.updated_utc = datetime.now(timezone.utc).isoformat()
    document.quick_open.counts = {
        "assets": len(document.assets),
        "nodes": len(document.nodes),
        "groups": len(document.groups),
        "locations": len(document.locations),
        "preview_blobs": len(document.preview_refs),
    }
    if document.manifest.source:
        document.manifest.source["asset_count"] = len(document.assets)
    if document.quick_open.source:
        document.quick_open.source["asset_count"] = len(document.assets)


def _sorted_assets(assets: list[EasyFindAssetRef]) -> list[EasyFindAssetRef]:
    return sorted(assets, key=lambda a: a.asset_id)


def _sorted_nodes(nodes: list[EasyFindNode]) -> list[EasyFindNode]:
    return sorted(nodes, key=lambda n: n.node_id)


def _sorted_locations(locations: list[EasyFindLocation]) -> list[EasyFindLocation]:
    return sorted(locations, key=_location_sort_key)


def _sorted_asset_tags(tags: list[EasyFindAssetTag]) -> list[EasyFindAssetTag]:
    return sorted(tags, key=lambda t: t.node_id)


def _sorted_manual_merges(merges: list[EasyFindManualMerge]) -> list[EasyFindManualMerge]:
    return sorted(merges, key=lambda m: m.merge_id)


def _sorted_preview_refs(refs: list[EasyFindPreviewRef]) -> list[EasyFindPreviewRef]:
    return sorted(refs, key=lambda r: r.preview_id)


def _sorted_color_signatures(sigs: list[EasyFindColorSignature]) -> list[EasyFindColorSignature]:
    return sorted(sigs, key=lambda s: (s.node_id, s.signature_id))


def _write_document_to_zip(
    zf: zipfile.ZipFile,
    document: EasyFindDocument,
    *,
    preview_blobs: Mapping[str, bytes] | None = None,
    progress: Progress | None = None,
) -> None:
    blobs = dict(preview_blobs or {})

    if progress:
        progress("Writing manifest…")
    zf.writestr(MANIFEST_PATH, dumps_json(document.manifest.to_dict()))

    if progress:
        progress("Writing index…")
    assets = _sorted_assets(document.assets)
    nodes = _sorted_nodes(document.nodes)
    zf.writestr(ASSETS_PATH, "".join(dumps_jsonl_line(a.to_dict()) for a in assets))
    zf.writestr(NODES_PATH, "".join(dumps_jsonl_line(n.to_dict()) for n in nodes))
    zf.writestr(GROUPS_PATH, dumps_json(document.groups))
    zf.writestr(LAYOUT_PATH, dumps_json(document.layout))

    if progress:
        progress("Writing annotations…")
    locations = _sorted_locations(document.locations)
    zf.writestr(LOCATIONS_PATH, dumps_json({"locations": [loc.to_dict() for loc in locations]}))
    asset_tags = _sorted_asset_tags(document.asset_tags)
    zf.writestr(ASSET_TAGS_PATH, dumps_json({"tags": [t.to_dict() for t in asset_tags]}))
    manual_merges = _sorted_manual_merges(document.manual_merges)
    zf.writestr(
        MANUAL_MERGES_PATH,
        dumps_json({"merges": [m.to_dict() for m in manual_merges]}),
    )
    zf.writestr(NOTES_PATH, dumps_json(document.notes))

    color_sigs = _sorted_color_signatures(document.color_signatures)
    zf.writestr(
        COLOR_INDEX_PATH,
        "".join(dumps_jsonl_line(s.to_dict()) for s in color_sigs),
    )

    if progress:
        progress("Writing preview index…")
    preview_refs = _sorted_preview_refs(document.preview_refs)
    zf.writestr(
        PREVIEWS_INDEX_PATH,
        dumps_json({"previews": [p.to_dict() for p in preview_refs]}),
    )

    if progress:
        progress("Validating preview hashes…")
    for preview in preview_refs:
        blob_path = preview.blob_path
        if blob_path in blobs:
            blob_bytes = blobs[blob_path]
        else:
            blob_name = Path(blob_path).name
            if blob_name in blobs:
                blob_bytes = blobs[blob_name]
            elif preview.preview_id in blobs:
                blob_bytes = blobs[preview.preview_id]
            else:
                continue
        actual_hash = hashlib.sha256(blob_bytes).hexdigest()
        if actual_hash != preview.sha256:
            raise EasyFindCorruptError(
                f"Preview blob hash mismatch for preview_id={preview.preview_id}"
            )
        zf.writestr(blob_path, blob_bytes)

    for blob_path, blob_bytes in blobs.items():
        if not blob_path.startswith(PREVIEW_BLOBS_DIR):
            full_path = f"{PREVIEW_BLOBS_DIR}/{blob_path}"
        else:
            full_path = blob_path
        if full_path not in zf.namelist():
            zf.writestr(full_path, blob_bytes)

    zf.writestr(QUICK_OPEN_PATH, dumps_json(document.quick_open.to_dict()))
    zf.writestr(BUILD_INFO_PATH, dumps_json(document.build_info))
    zf.writestr(BUILD_LOG_PATH, document.build_log)


def save_easyfind(
    path: str | Path,
    document: EasyFindDocument,
    *,
    preview_blobs: Mapping[str, bytes] | None = None,
    progress: Progress | None = None,
) -> Path:
    """Write an EasyFind file atomically."""
    target = Path(path)
    if target.suffix.lower() != EASYFIND_EXTENSION:
        target = target.with_suffix(EASYFIND_EXTENSION)
    target.parent.mkdir(parents=True, exist_ok=True)

    _sync_manifest_counts(document)

    if progress:
        progress("Creating output container…")

    temp_path = target.with_suffix(target.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
            if _write_failure_hook is not None:
                _write_failure_hook()
            _write_document_to_zip(zf, document, preview_blobs=preview_blobs, progress=progress)

        if progress:
            progress("Finalizing EasyFind file…")
        os.replace(temp_path, target)
    except Exception:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)
        raise

    return target


def load_easyfind_quick_open(path: str | Path) -> EasyFindQuickOpen:
    """Read only manifest.json and quick_open.json for fast UI startup."""
    source = Path(path)
    try:
        with zipfile.ZipFile(source, "r") as zf:
            names = set(zf.namelist())
            if MANIFEST_PATH not in names or QUICK_OPEN_PATH not in names:
                raise EasyFindCorruptError("EasyFind file is missing required metadata.")
            loads_json(zf.read(MANIFEST_PATH).decode("utf-8"))
            quick_data = loads_json(zf.read(QUICK_OPEN_PATH).decode("utf-8"))
    except EasyFindError:
        raise
    except Exception as exc:
        raise EasyFindCorruptError(f"Cannot read EasyFind metadata: {exc}") from exc
    return EasyFindQuickOpen.from_dict(quick_data)


def load_easyfind(path: str | Path) -> EasyFindDocument:
    """Load document metadata and preview index. Preview bytes remain lazy."""
    source = Path(path)
    try:
        with zipfile.ZipFile(source, "r") as zf:
            manifest_data = loads_json(zf.read(MANIFEST_PATH).decode("utf-8"))
            quick_data = loads_json(zf.read(QUICK_OPEN_PATH).decode("utf-8"))

            assets = [
                EasyFindAssetRef.from_dict(r)
                for r in parse_jsonl(zf.read(ASSETS_PATH).decode("utf-8"))
            ]
            nodes = [
                EasyFindNode.from_dict(r)
                for r in parse_jsonl(zf.read(NODES_PATH).decode("utf-8"))
            ]

            groups_raw = loads_json(zf.read(GROUPS_PATH).decode("utf-8"))
            groups = groups_raw if isinstance(groups_raw, list) else []

            layout = loads_json(zf.read(LAYOUT_PATH).decode("utf-8"))
            if not isinstance(layout, dict):
                layout = {}

            locations_raw = loads_json(zf.read(LOCATIONS_PATH).decode("utf-8"))
            if isinstance(locations_raw, dict):
                locations = [
                    EasyFindLocation.from_dict(loc)
                    for loc in locations_raw.get("locations", [])
                ]
            else:
                locations = [EasyFindLocation.from_dict(loc) for loc in locations_raw]

            tags_raw = loads_json(zf.read(ASSET_TAGS_PATH).decode("utf-8"))
            if isinstance(tags_raw, dict):
                asset_tags = [
                    EasyFindAssetTag.from_dict(t) for t in tags_raw.get("tags", [])
                ]
            else:
                asset_tags = [EasyFindAssetTag.from_dict(t) for t in tags_raw]

            merges_raw = loads_json(zf.read(MANUAL_MERGES_PATH).decode("utf-8"))
            if isinstance(merges_raw, dict):
                manual_merges = [
                    EasyFindManualMerge.from_dict(m) for m in merges_raw.get("merges", [])
                ]
            else:
                manual_merges = [EasyFindManualMerge.from_dict(m) for m in merges_raw]

            notes = loads_json(zf.read(NOTES_PATH).decode("utf-8"))
            if not isinstance(notes, dict):
                notes = {}

            color_signatures = [
                EasyFindColorSignature.from_dict(r)
                for r in parse_jsonl(zf.read(COLOR_INDEX_PATH).decode("utf-8"))
            ]

            previews_raw = loads_json(zf.read(PREVIEWS_INDEX_PATH).decode("utf-8"))
            if isinstance(previews_raw, dict):
                preview_refs = [
                    EasyFindPreviewRef.from_dict(p)
                    for p in previews_raw.get("previews", [])
                ]
            else:
                preview_refs = [EasyFindPreviewRef.from_dict(p) for p in previews_raw]

            build_info = loads_json(zf.read(BUILD_INFO_PATH).decode("utf-8"))
            if not isinstance(build_info, dict):
                build_info = {}

            build_log = zf.read(BUILD_LOG_PATH).decode("utf-8")
    except EasyFindError:
        raise
    except Exception as exc:
        raise EasyFindCorruptError(f"Cannot load EasyFind document: {exc}") from exc

    from .models import EasyFindManifest

    return EasyFindDocument(
        manifest=EasyFindManifest.from_dict(manifest_data),
        quick_open=EasyFindQuickOpen.from_dict(quick_data),
        assets=assets,
        nodes=nodes,
        groups=groups,
        layout=layout,
        locations=locations,
        asset_tags=asset_tags,
        manual_merges=manual_merges,
        notes=notes,
        color_signatures=color_signatures,
        preview_refs=preview_refs,
        build_info=build_info,
        build_log=build_log,
    )


def read_easyfind_preview(path: str | Path, preview_id: str) -> bytes:
    """Read one preview blob by preview ID."""
    source = Path(path)
    try:
        with zipfile.ZipFile(source, "r") as zf:
            previews_raw = loads_json(zf.read(PREVIEWS_INDEX_PATH).decode("utf-8"))
            if isinstance(previews_raw, dict):
                preview_list = previews_raw.get("previews", [])
            else:
                preview_list = previews_raw

            blob_path = None
            for preview in preview_list:
                if str(preview.get("preview_id")) == preview_id:
                    blob_path = str(preview.get("blob_path", ""))
                    break

            if blob_path is None:
                raise EasyFindError(f"Unknown preview ID: {preview_id}")
            if blob_path not in zf.namelist():
                raise EasyFindCorruptError(
                    f"Preview blob missing for preview_id={preview_id}"
                )
            return zf.read(blob_path)
    except EasyFindError:
        raise
    except Exception as exc:
        raise EasyFindCorruptError(f"Cannot read preview: {exc}") from exc
