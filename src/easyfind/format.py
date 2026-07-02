"""EasyFind format constants and JSON helpers."""
from __future__ import annotations

import json
from typing import Any

EASYFIND_FORMAT = "rae-easyfind-v1"
EASYFIND_SCHEMA_VERSION = 1
EASYFIND_EXTENSION = ".easyfind"

MANIFEST_PATH = "manifest.json"
QUICK_OPEN_PATH = "quick_open.json"

ASSETS_PATH = "index/assets.jsonl"
NODES_PATH = "index/nodes.jsonl"
GROUPS_PATH = "index/groups.json"
LAYOUT_PATH = "index/layout.json"

LOCATIONS_PATH = "annotations/locations.json"
ASSET_TAGS_PATH = "annotations/asset_tags.json"
MANUAL_MERGES_PATH = "annotations/manual_merges.json"
NOTES_PATH = "annotations/notes.json"

COLOR_INDEX_PATH = "signatures/color_index.jsonl"
BUCKET_LOOKUP_PATH = "signatures/bucket_lookup.json"
BUCKET_LOOKUP_VERSION = 2

PREVIEWS_INDEX_PATH = "previews/index.json"
PREVIEW_BLOBS_DIR = "previews/blobs"

BUILD_INFO_PATH = "build/build_info.json"
BUILD_LOG_PATH = "build/log.txt"

REQUIRED_PATHS: tuple[str, ...] = (
    MANIFEST_PATH,
    QUICK_OPEN_PATH,
    ASSETS_PATH,
    NODES_PATH,
    GROUPS_PATH,
    LAYOUT_PATH,
    LOCATIONS_PATH,
    ASSET_TAGS_PATH,
    MANUAL_MERGES_PATH,
    NOTES_PATH,
    COLOR_INDEX_PATH,
    BUCKET_LOOKUP_PATH,
    PREVIEWS_INDEX_PATH,
    BUILD_INFO_PATH,
    BUILD_LOG_PATH,
)

JSONL_PATHS: frozenset[str] = frozenset({
    ASSETS_PATH,
    NODES_PATH,
    COLOR_INDEX_PATH,
})


def dumps_json(data: Any, *, indent: int | None = 2) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=False) + "\n"


def loads_json(text: str) -> Any:
    return json.loads(text)


def dumps_jsonl_line(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False) + "\n"


def parse_jsonl(text: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            records.append(json.loads(stripped))
    return records
