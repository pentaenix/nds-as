"""EasyFind structural validation."""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from .format import (
    ASSET_TAGS_PATH,
    ASSETS_PATH,
    BUILD_INFO_PATH,
    BUILD_LOG_PATH,
    COLOR_INDEX_PATH,
    EASYFIND_FORMAT,
    EASYFIND_SCHEMA_VERSION,
    GROUPS_PATH,
    JSONL_PATHS,
    LAYOUT_PATH,
    LOCATIONS_PATH,
    MANIFEST_PATH,
    MANUAL_MERGES_PATH,
    NOTES_PATH,
    NODES_PATH,
    PREVIEW_BLOBS_DIR,
    PREVIEWS_INDEX_PATH,
    QUICK_OPEN_PATH,
    REQUIRED_PATHS,
    loads_json,
    parse_jsonl,
)
from .models import EasyFindValidationReport


class EasyFindError(Exception):
    """Base EasyFind error."""


class EasyFindUnsupportedFormatError(EasyFindError):
    """Raised when the EasyFind format or schema is not supported."""


class EasyFindCorruptError(EasyFindError):
    """Raised when an EasyFind file is corrupt or unreadable."""


class EasyFindValidationError(EasyFindError):
    """Raised when validation fails."""


def validate_easyfind(path: str | Path) -> EasyFindValidationReport:
    """Validate structure, schema, JSON, references, counts, and preview hashes."""
    source = Path(path)
    errors: list[str] = []
    warnings: list[str] = []
    counts: dict[str, int] = {}

    try:
        zf = zipfile.ZipFile(source, "r")
    except Exception as exc:
        return EasyFindValidationReport(
            ok=False,
            errors=[f"Cannot open EasyFind file: {exc}"],
            warnings=[],
            counts={},
        )

    with zf:
        names = set(zf.namelist())

        for required in REQUIRED_PATHS:
            if required not in names:
                errors.append(f"Missing required file: {required}")

        manifest = None
        if MANIFEST_PATH in names:
            try:
                manifest = loads_json(zf.read(MANIFEST_PATH).decode("utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid JSON in {MANIFEST_PATH}: {exc}")

        quick_open = None
        if QUICK_OPEN_PATH in names:
            try:
                quick_open = loads_json(zf.read(QUICK_OPEN_PATH).decode("utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid JSON in {QUICK_OPEN_PATH}: {exc}")

        if manifest is not None:
            fmt = manifest.get("format")
            if fmt != EASYFIND_FORMAT:
                errors.append(f"Unsupported EasyFind format: {fmt}")
            schema = manifest.get("schema_version")
            if schema != EASYFIND_SCHEMA_VERSION:
                errors.append(f"Unsupported EasyFind schema version: {schema}")

        if manifest is not None and quick_open is not None:
            if quick_open.get("format") != manifest.get("format"):
                errors.append("quick_open.json format does not match manifest.json")
            if quick_open.get("schema_version") != manifest.get("schema_version"):
                errors.append("quick_open.json schema_version does not match manifest.json")

        json_paths = [
            GROUPS_PATH,
            LAYOUT_PATH,
            LOCATIONS_PATH,
            ASSET_TAGS_PATH,
            MANUAL_MERGES_PATH,
            NOTES_PATH,
            PREVIEWS_INDEX_PATH,
            BUILD_INFO_PATH,
        ]
        parsed_json: dict[str, object] = {}
        for jp in json_paths:
            if jp not in names:
                continue
            try:
                parsed_json[jp] = loads_json(zf.read(jp).decode("utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid JSON in {jp}: {exc}")

        if BUILD_LOG_PATH in names:
            try:
                zf.read(BUILD_LOG_PATH).decode("utf-8")
            except UnicodeDecodeError as exc:
                errors.append(f"Invalid UTF-8 in {BUILD_LOG_PATH}: {exc}")

        jsonl_data: dict[str, list[dict]] = {}
        for jlp in JSONL_PATHS:
            if jlp not in names:
                continue
            try:
                jsonl_data[jlp] = parse_jsonl(zf.read(jlp).decode("utf-8"))
            except json.JSONDecodeError as exc:
                errors.append(f"Invalid JSONL in {jlp}: {exc}")

        assets = jsonl_data.get(ASSETS_PATH, [])
        nodes = jsonl_data.get(NODES_PATH, [])
        color_sigs = jsonl_data.get(COLOR_INDEX_PATH, [])

        asset_ids = {str(a["asset_id"]) for a in assets}
        node_ids = {str(n["node_id"]) for n in nodes}

        counts["assets"] = len(assets)
        counts["nodes"] = len(nodes)
        counts["color_signatures"] = len(color_sigs)

        locations_raw = parsed_json.get(LOCATIONS_PATH, [])
        if isinstance(locations_raw, dict):
            locations = locations_raw.get("locations", [])
        elif isinstance(locations_raw, list):
            locations = locations_raw
        else:
            locations = []
        location_ids = {str(loc["location_id"]) for loc in locations if isinstance(loc, dict)}
        counts["locations"] = len(location_ids)

        groups_raw = parsed_json.get(GROUPS_PATH, [])
        counts["groups"] = len(groups_raw) if isinstance(groups_raw, list) else 0

        asset_tags_raw = parsed_json.get(ASSET_TAGS_PATH, [])
        if isinstance(asset_tags_raw, dict):
            asset_tags = asset_tags_raw.get("tags", [])
        elif isinstance(asset_tags_raw, list):
            asset_tags = asset_tags_raw
        else:
            asset_tags = []

        manual_merges_raw = parsed_json.get(MANUAL_MERGES_PATH, [])
        if isinstance(manual_merges_raw, dict):
            manual_merges = manual_merges_raw.get("merges", [])
        elif isinstance(manual_merges_raw, list):
            manual_merges = manual_merges_raw
        else:
            manual_merges = []

        counts["manual_merges"] = len(manual_merges)

        preview_refs_raw = parsed_json.get(PREVIEWS_INDEX_PATH, [])
        if isinstance(preview_refs_raw, dict):
            preview_refs = preview_refs_raw.get("previews", [])
        elif isinstance(preview_refs_raw, list):
            preview_refs = preview_refs_raw
        else:
            preview_refs = []

        counts["preview_blobs"] = len(preview_refs)

        if manifest is not None:
            manifest_counts = manifest.get("counts", {})
            for key in ("assets", "nodes", "groups", "locations", "manual_merges", "preview_blobs", "color_signatures"):
                expected = manifest_counts.get(key)
                if expected is not None and int(expected) != counts.get(key, 0):
                    errors.append(
                        f"Manifest count mismatch for {key}: "
                        f"manifest={expected}, actual={counts.get(key, 0)}"
                    )

        for node in nodes:
            node_id = str(node.get("node_id", ""))
            for ref in node.get("asset_refs", []):
                ref_asset_id = str(ref.get("asset_id", ""))
                if ref_asset_id not in asset_ids:
                    errors.append(
                        f"Node {node_id} references missing asset: {ref_asset_id}"
                    )

        for sig in color_sigs:
            sig_node = str(sig.get("node_id", ""))
            if sig_node not in node_ids:
                errors.append(
                    f"Color signature {sig.get('signature_id', '')} "
                    f"references missing node: {sig_node}"
                )

        for tag in asset_tags:
            if not isinstance(tag, dict):
                continue
            tag_node = str(tag.get("node_id", ""))
            if tag_node not in node_ids:
                errors.append(
                    f"Asset tag references missing node: {tag_node}"
                )
            loc_id = tag.get("location_id")
            if loc_id is not None and str(loc_id) not in location_ids:
                errors.append(
                    f"Asset tag for node {tag_node} references missing location: {loc_id}"
                )

        for merge in manual_merges:
            if not isinstance(merge, dict):
                continue
            merge_id = str(merge.get("merge_id", ""))
            for member_id in merge.get("member_node_ids", []):
                if str(member_id) not in node_ids:
                    errors.append(
                        f"Manual merge {merge_id} references missing node: {member_id}"
                    )

        for preview in preview_refs:
            if not isinstance(preview, dict):
                continue
            preview_id = str(preview.get("preview_id", ""))
            blob_path = str(preview.get("blob_path", ""))
            expected_hash = str(preview.get("sha256", ""))
            preview_node = str(preview.get("node_id", ""))
            if preview_node not in node_ids:
                errors.append(
                    f"Preview {preview_id} references missing node: {preview_node}"
                )
            if blob_path not in names:
                errors.append(f"Missing preview blob for preview_id={preview_id}")
                continue
            blob_bytes = zf.read(blob_path)
            actual_hash = hashlib.sha256(blob_bytes).hexdigest()
            if actual_hash != expected_hash:
                errors.append(
                    f"Preview blob hash mismatch for preview_id={preview_id}"
                )

    return EasyFindValidationReport(
        ok=len(errors) == 0,
        errors=errors,
        warnings=warnings,
        counts=counts,
    )
