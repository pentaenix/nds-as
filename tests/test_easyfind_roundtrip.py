import hashlib
from datetime import datetime, timezone

from rae.easyfind import (
    create_easyfind_document,
    load_easyfind,
    load_node_index,
    read_easyfind_preview,
    save_easyfind,
)
from rae.easyfind.format import BUCKET_LOOKUP_PATH
from tests.easyfind_testutil import finalize_easyfind_document
from rae.easyfind.models import (
    EasyFindAssetTag,
    EasyFindColorSignature,
    EasyFindLocation,
    EasyFindManualMerge,
    EasyFindPreviewRef,
)
from rae.scanner import Asset


def _sample_asset(asset_id: str = "a1") -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=f"path/{asset_id}.nsbmd",
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0demo",
        original_data=b"BMD0demo",
    )


def test_save_load_roundtrip(tmp_path):
    doc = create_easyfind_document(
        assets=[_sample_asset("a1"), _sample_asset("a2")],
        rom_path="game.nds",
    )
    doc.groups = [{"group_id": "g1", "label": "Models"}]
    doc.layout = {"version": 1, "nodes": {}}
    doc.locations = [
        EasyFindLocation(
            location_id="loc1",
            name="Town",
            group="areas",
            kind="region",
            order=1,
            color="#ff0000",
            aliases=["town"],
            metadata={},
        )
    ]
    doc.asset_tags = [
        EasyFindAssetTag(
            node_id="asset:a1",
            location_id="loc1",
            tags=["hero"],
            favorite=True,
            note_id="note1",
            metadata={},
        )
    ]
    doc.manual_merges = [
        EasyFindManualMerge(
            merge_id="merge1",
            label="Stack",
            member_node_ids=["asset:a1", "asset:a2"],
            created_utc=datetime.now(timezone.utc).isoformat(),
            reason="manual",
            metadata={},
        )
    ]
    doc.notes = {"note1": {"text": "Important asset"}}
    doc.color_signatures = [
        EasyFindColorSignature(
            signature_id="sig1",
            node_id="asset:a1",
            dominant_bucket="red",
            dominant_colors=["#ff0000"],
            secondary_buckets=["orange"],
            brightness="mid",
            saturation="high",
            transparent_ratio=0.1,
            metadata={},
        )
    ]
    blob = b"\x89PNG\r\n\x1a\nfake"
    blob_hash = hashlib.sha256(blob).hexdigest()
    blob_path = f"previews/blobs/{blob_hash}.png"
    doc.preview_refs = [
        EasyFindPreviewRef(
            preview_id="prev1",
            node_id="asset:a1",
            kind="thumbnail",
            mime_type="image/png",
            blob_path=blob_path,
            sha256=blob_hash,
            width=64,
            height=64,
            byte_size=len(blob),
        )
    ]
    doc.build_info = {"builder": "rae", "note": "test"}
    doc.build_log = "Build log line 1\nBuild log line 2\n"

    finalize_easyfind_document(doc)
    path = save_easyfind(tmp_path / "game.easyfind", doc, preview_blobs={blob_path: blob})
    loaded = load_easyfind(path)

    assert loaded.manifest.format == doc.manifest.format
    assert len(loaded.assets) == 2
    assert len(loaded.nodes) == 2
    assert loaded.groups == doc.groups
    assert loaded.layout == doc.layout
    assert len(loaded.locations) == 1
    assert loaded.locations[0].location_id == "loc1"
    assert len(loaded.asset_tags) == 1
    assert loaded.asset_tags[0].tags == ["hero"]
    assert len(loaded.manual_merges) == 1
    assert loaded.manual_merges[0].merge_id == "merge1"
    assert loaded.notes == doc.notes
    assert len(loaded.color_signatures) == 1
    assert loaded.color_signatures[0].signature_id == "sig1"
    assert len(loaded.preview_refs) == 1
    assert loaded.preview_refs[0].preview_id == "prev1"
    assert loaded.build_info == doc.build_info
    assert loaded.build_log == doc.build_log

    preview_bytes = read_easyfind_preview(path, "prev1")
    assert preview_bytes == blob
    assert loaded.bucket_lookup is not None
    assert len(load_node_index(loaded).by_primary_bucket) >= 1
    with __import__("zipfile").ZipFile(path, "r") as zf:
        assert BUCKET_LOOKUP_PATH in zf.namelist()
