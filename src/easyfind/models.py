"""EasyFind document data models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .format import EASYFIND_FORMAT, EASYFIND_SCHEMA_VERSION


@dataclass(frozen=True)
class EasyFindIdentity:
    asset_id: str
    virtual_path: str
    magic: str
    rom_file_id: int | None
    rom_offset: int | None
    size: int
    container_chain: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "virtual_path": self.virtual_path,
            "magic": self.magic,
            "rom_file_id": self.rom_file_id,
            "rom_offset": self.rom_offset,
            "size": self.size,
            "container_chain": list(self.container_chain),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindIdentity:
        return cls(
            asset_id=str(data["asset_id"]),
            virtual_path=str(data["virtual_path"]),
            magic=str(data["magic"]),
            rom_file_id=data.get("rom_file_id"),
            rom_offset=data.get("rom_offset"),
            size=int(data["size"]),
            container_chain=tuple(str(x) for x in data.get("container_chain", [])),
        )


@dataclass
class EasyFindAssetRef:
    asset_id: str
    virtual_path: str
    kind: str
    magic: str
    extension: str
    rom_file_id: int | None
    rom_offset: int | None
    size: int
    original_size: int
    compressed: bool
    container_chain: tuple[str, ...]
    carved: bool
    carved_offset: int | None
    mapping_category: str
    mapping_label: str
    mapping_confidence: str
    identity: EasyFindIdentity

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "virtual_path": self.virtual_path,
            "kind": self.kind,
            "magic": self.magic,
            "extension": self.extension,
            "rom_file_id": self.rom_file_id,
            "rom_offset": self.rom_offset,
            "size": self.size,
            "original_size": self.original_size,
            "compressed": self.compressed,
            "container_chain": list(self.container_chain),
            "carved": self.carved,
            "carved_offset": self.carved_offset,
            "mapping_category": self.mapping_category,
            "mapping_label": self.mapping_label,
            "mapping_confidence": self.mapping_confidence,
            "identity": self.identity.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindAssetRef:
        return cls(
            asset_id=str(data["asset_id"]),
            virtual_path=str(data["virtual_path"]),
            kind=str(data["kind"]),
            magic=str(data["magic"]),
            extension=str(data["extension"]),
            rom_file_id=data.get("rom_file_id"),
            rom_offset=data.get("rom_offset"),
            size=int(data["size"]),
            original_size=int(data["original_size"]),
            compressed=bool(data.get("compressed", False)),
            container_chain=tuple(str(x) for x in data.get("container_chain", [])),
            carved=bool(data.get("carved", False)),
            carved_offset=data.get("carved_offset"),
            mapping_category=str(data.get("mapping_category", "unknown")),
            mapping_label=str(data.get("mapping_label", "")),
            mapping_confidence=str(data.get("mapping_confidence", "")),
            identity=EasyFindIdentity.from_dict(data["identity"]),
        )


@dataclass
class EasyFindNodeAssetRef:
    asset_id: str
    sub_id: str | None
    role: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "sub_id": self.sub_id,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindNodeAssetRef:
        return cls(
            asset_id=str(data["asset_id"]),
            sub_id=data.get("sub_id"),
            role=str(data["role"]),
        )


@dataclass
class EasyFindNode:
    node_id: str
    node_kind: str
    label: str
    asset_refs: list[EasyFindNodeAssetRef]
    parent_node_id: str | None
    child_node_ids: list[str]
    preview_ref: str | None
    color_signature_ref: str | None
    layout_ref: str | None
    visibility: str
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "node_kind": self.node_kind,
            "label": self.label,
            "asset_refs": [ref.to_dict() for ref in self.asset_refs],
            "parent_node_id": self.parent_node_id,
            "child_node_ids": list(self.child_node_ids),
            "preview_ref": self.preview_ref,
            "color_signature_ref": self.color_signature_ref,
            "layout_ref": self.layout_ref,
            "visibility": self.visibility,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindNode:
        return cls(
            node_id=str(data["node_id"]),
            node_kind=str(data["node_kind"]),
            label=str(data["label"]),
            asset_refs=[EasyFindNodeAssetRef.from_dict(r) for r in data.get("asset_refs", [])],
            parent_node_id=data.get("parent_node_id"),
            child_node_ids=[str(x) for x in data.get("child_node_ids", [])],
            preview_ref=data.get("preview_ref"),
            color_signature_ref=data.get("color_signature_ref"),
            layout_ref=data.get("layout_ref"),
            visibility=str(data.get("visibility", "normal")),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class EasyFindPreviewRef:
    preview_id: str
    node_id: str
    kind: str
    mime_type: str
    blob_path: str
    sha256: str
    width: int | None
    height: int | None
    byte_size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "preview_id": self.preview_id,
            "node_id": self.node_id,
            "kind": self.kind,
            "mime_type": self.mime_type,
            "blob_path": self.blob_path,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindPreviewRef:
        return cls(
            preview_id=str(data["preview_id"]),
            node_id=str(data["node_id"]),
            kind=str(data["kind"]),
            mime_type=str(data["mime_type"]),
            blob_path=str(data["blob_path"]),
            sha256=str(data["sha256"]),
            width=data.get("width"),
            height=data.get("height"),
            byte_size=int(data["byte_size"]),
        )


@dataclass
class EasyFindColorSignature:
    signature_id: str
    node_id: str
    dominant_bucket: str
    dominant_colors: list[str]
    secondary_buckets: list[str]
    brightness: str
    saturation: str
    transparent_ratio: float | None
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signature_id": self.signature_id,
            "node_id": self.node_id,
            "dominant_bucket": self.dominant_bucket,
            "dominant_colors": list(self.dominant_colors),
            "secondary_buckets": list(self.secondary_buckets),
            "brightness": self.brightness,
            "saturation": self.saturation,
            "transparent_ratio": self.transparent_ratio,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindColorSignature:
        return cls(
            signature_id=str(data["signature_id"]),
            node_id=str(data["node_id"]),
            dominant_bucket=str(data["dominant_bucket"]),
            dominant_colors=[str(x) for x in data.get("dominant_colors", [])],
            secondary_buckets=[str(x) for x in data.get("secondary_buckets", [])],
            brightness=str(data["brightness"]),
            saturation=str(data["saturation"]),
            transparent_ratio=data.get("transparent_ratio"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class EasyFindLocation:
    location_id: str
    name: str
    group: str
    kind: str
    order: int | None
    color: str | None
    aliases: list[str]
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "location_id": self.location_id,
            "name": self.name,
            "group": self.group,
            "kind": self.kind,
            "order": self.order,
            "color": self.color,
            "aliases": list(self.aliases),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindLocation:
        return cls(
            location_id=str(data["location_id"]),
            name=str(data["name"]),
            group=str(data["group"]),
            kind=str(data["kind"]),
            order=data.get("order"),
            color=data.get("color"),
            aliases=[str(x) for x in data.get("aliases", [])],
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class EasyFindAssetTag:
    node_id: str
    location_id: str | None
    tags: list[str]
    favorite: bool
    note_id: str | None
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "location_id": self.location_id,
            "tags": list(self.tags),
            "favorite": self.favorite,
            "note_id": self.note_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindAssetTag:
        return cls(
            node_id=str(data["node_id"]),
            location_id=data.get("location_id"),
            tags=[str(x) for x in data.get("tags", [])],
            favorite=bool(data.get("favorite", False)),
            note_id=data.get("note_id"),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class EasyFindManualMerge:
    merge_id: str
    label: str
    member_node_ids: list[str]
    created_utc: str
    reason: str
    metadata: dict[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "merge_id": self.merge_id,
            "label": self.label,
            "member_node_ids": list(self.member_node_ids),
            "created_utc": self.created_utc,
            "reason": self.reason,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindManualMerge:
        return cls(
            merge_id=str(data["merge_id"]),
            label=str(data["label"]),
            member_node_ids=[str(x) for x in data.get("member_node_ids", [])],
            created_utc=str(data["created_utc"]),
            reason=str(data["reason"]),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class EasyFindManifest:
    format: str
    schema_version: int
    created_utc: str
    updated_utc: str
    source: dict[str, object]
    counts: dict[str, int]
    capabilities: dict[str, bool]
    build: dict[str, object]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "created_utc": self.created_utc,
            "updated_utc": self.updated_utc,
            "source": dict(self.source),
            "counts": dict(self.counts),
            "capabilities": dict(self.capabilities),
            "build": dict(self.build),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindManifest:
        return cls(
            format=str(data["format"]),
            schema_version=int(data["schema_version"]),
            created_utc=str(data["created_utc"]),
            updated_utc=str(data["updated_utc"]),
            source=dict(data.get("source", {})),
            counts={k: int(v) for k, v in dict(data.get("counts", {})).items()},
            capabilities={k: bool(v) for k, v in dict(data.get("capabilities", {})).items()},
            build=dict(data.get("build", {})),
        )


@dataclass
class EasyFindQuickOpen:
    format: str
    schema_version: int
    source: dict[str, object]
    counts: dict[str, int]
    default_view: dict[str, object]
    capabilities: dict[str, bool]

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "schema_version": self.schema_version,
            "source": dict(self.source),
            "counts": dict(self.counts),
            "default_view": dict(self.default_view),
            "capabilities": dict(self.capabilities),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindQuickOpen:
        return cls(
            format=str(data["format"]),
            schema_version=int(data["schema_version"]),
            source=dict(data.get("source", {})),
            counts={k: int(v) for k, v in dict(data.get("counts", {})).items()},
            default_view=dict(data.get("default_view", {})),
            capabilities={k: bool(v) for k, v in dict(data.get("capabilities", {})).items()},
        )


@dataclass
class EasyFindValidationReport:
    ok: bool
    errors: list[str]
    warnings: list[str]
    counts: dict[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
            "counts": dict(self.counts),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindValidationReport:
        return cls(
            ok=bool(data["ok"]),
            errors=[str(x) for x in data.get("errors", [])],
            warnings=[str(x) for x in data.get("warnings", [])],
            counts={k: int(v) for k, v in dict(data.get("counts", {})).items()},
        )


@dataclass
class EasyFindMatchReport:
    matched: dict[str, str]
    missing: list[str]
    ambiguous: dict[str, list[str]]
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "matched": dict(self.matched),
            "missing": list(self.missing),
            "ambiguous": {k: list(v) for k, v in self.ambiguous.items()},
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EasyFindMatchReport:
        return cls(
            matched={str(k): str(v) for k, v in dict(data.get("matched", {})).items()},
            missing=[str(x) for x in data.get("missing", [])],
            ambiguous={str(k): [str(x) for x in v] for k, v in dict(data.get("ambiguous", {})).items()},
            summary=str(data.get("summary", "")),
        )


@dataclass
class EasyFindDocument:
    manifest: EasyFindManifest
    quick_open: EasyFindQuickOpen
    assets: list[EasyFindAssetRef]
    nodes: list[EasyFindNode]
    groups: list[dict[str, object]]
    layout: dict[str, object]
    locations: list[EasyFindLocation]
    asset_tags: list[EasyFindAssetTag]
    manual_merges: list[EasyFindManualMerge]
    notes: dict[str, dict[str, object]]
    color_signatures: list[EasyFindColorSignature]
    preview_refs: list[EasyFindPreviewRef]
    build_info: dict[str, object]
    build_log: str
    # Precomputed bucket membership written to signatures/bucket_lookup.json at save time.
    bucket_lookup: dict[str, object] | None = None


def default_capabilities() -> dict[str, bool]:
    return {
        "contains_previews": True,
        "contains_color_signatures": True,
        "contains_bucket_lookup": True,
        "contains_layout": True,
        "contains_annotations": True,
    }


def default_quick_open_view() -> dict[str, object]:
    return {
        "group_by": ["color", "type"],
        "dependencies": "on_select",
        "labels": "hover_only",
    }


def empty_source(
    *,
    platform: str = "nds",
    rom_name: str = "",
    rom_path_note: str = "",
    rom_title: str = "",
    rom_game_code: str = "",
    asset_count: int = 0,
) -> dict[str, object]:
    return {
        "platform": platform,
        "rom_name": rom_name,
        "rom_path_note": rom_path_note,
        "rom_title": rom_title,
        "rom_game_code": rom_game_code,
        "asset_count": asset_count,
    }


def empty_counts() -> dict[str, int]:
    return {
        "assets": 0,
        "nodes": 0,
        "groups": 0,
        "locations": 0,
        "manual_merges": 0,
        "preview_blobs": 0,
        "color_signatures": 0,
    }


def quick_open_counts_from_manifest(counts: dict[str, int]) -> dict[str, int]:
    return {
        "assets": counts.get("assets", 0),
        "nodes": counts.get("nodes", 0),
        "groups": counts.get("groups", 0),
        "locations": counts.get("locations", 0),
        "preview_blobs": counts.get("preview_blobs", 0),
    }
