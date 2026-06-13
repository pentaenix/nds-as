import hashlib
import json
import zipfile

from rae.easyfind import (
    EasyFindError,
    create_easyfind_document,
    read_easyfind_preview,
    save_easyfind,
    validate_easyfind,
)
from rae.easyfind.format import (
    ASSET_TAGS_PATH,
    ASSETS_PATH,
    EASYFIND_FORMAT,
    MANIFEST_PATH,
    MANUAL_MERGES_PATH,
    NODES_PATH,
    PREVIEWS_INDEX_PATH,
    dumps_json,
)
from rae.easyfind.models import EasyFindAssetTag, EasyFindManualMerge, EasyFindPreviewRef
from rae.easyfind.store import _set_write_failure_hook
from rae.scanner import Asset
from tests.easyfind_testutil import finalize_easyfind_document


def _asset(asset_id: str = "a1") -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=f"{asset_id}.nsbmd",
        kind="Model",
        magic="BMD0",
        extension=".nsbmd",
        data=b"BMD0",
        original_data=b"BMD0",
    )


def _valid_file(tmp_path):
    doc = finalize_easyfind_document(
        create_easyfind_document(assets=[_asset()], rom_path="game.nds"),
    )
    return save_easyfind(tmp_path / "valid.easyfind", doc)


def test_validation_success(tmp_path):
    path = _valid_file(tmp_path)
    report = validate_easyfind(path)
    assert report.ok is True
    assert report.errors == []
    assert report.counts["assets"] == 1
    assert report.counts["nodes"] == 1


def test_validation_missing_manifest(tmp_path):
    path = tmp_path / "bad.easyfind"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("quick_open.json", "{}")
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("manifest.json" in e for e in report.errors)


def test_validation_unsupported_format(tmp_path):
    path = tmp_path / "bad.easyfind"
    manifest = {
        "format": "other-format",
        "schema_version": 1,
        "counts": {},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(MANIFEST_PATH, dumps_json(manifest))
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("Unsupported EasyFind format" in e for e in report.errors)


def test_validation_unsupported_schema(tmp_path):
    path = tmp_path / "bad.easyfind"
    manifest = {
        "format": EASYFIND_FORMAT,
        "schema_version": 99,
        "counts": {},
    }
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(MANIFEST_PATH, dumps_json(manifest))
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("Unsupported EasyFind schema version" in e for e in report.errors)


def test_validation_bad_json(tmp_path):
    path = _valid_file(tmp_path)
    with zipfile.ZipFile(path, "a") as zf:
        pass
    data = path.read_bytes()
    path.write_bytes(data.replace(b'"schema_version": 1', b'"schema_version": 1', 1))
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr("index/groups.json", "{not json")
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("Invalid JSON" in e for e in report.errors)


def test_validation_missing_preview_blob(tmp_path):
    doc = finalize_easyfind_document(
        create_easyfind_document(assets=[_asset()], rom_path="game.nds"),
    )
    blob = b"png"
    blob_hash = hashlib.sha256(blob).hexdigest()
    doc.preview_refs = [
        EasyFindPreviewRef(
            preview_id="p1",
            node_id="asset:a1",
            kind="thumb",
            mime_type="image/png",
            blob_path=f"previews/blobs/{blob_hash}.png",
            sha256=blob_hash,
            width=1,
            height=1,
            byte_size=len(blob),
        )
    ]
    path = save_easyfind(tmp_path / "no_blob.easyfind", doc)
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("Missing preview blob" in e for e in report.errors)


def test_validation_preview_hash_mismatch(tmp_path):
    doc = finalize_easyfind_document(
        create_easyfind_document(assets=[_asset()], rom_path="game.nds"),
    )
    blob = b"png"
    wrong_hash = "0" * 64
    blob_path = f"previews/blobs/{wrong_hash}.png"
    doc.preview_refs = [
        EasyFindPreviewRef(
            preview_id="p1",
            node_id="asset:a1",
            kind="thumb",
            mime_type="image/png",
            blob_path=blob_path,
            sha256=wrong_hash,
            width=1,
            height=1,
            byte_size=len(blob),
        )
    ]
    try:
        save_easyfind(tmp_path / "hash_bad.easyfind", doc, preview_blobs={blob_path: blob})
        raised = False
    except Exception:
        raised = True
    assert raised

    path = tmp_path / "hash_bad2.easyfind"
    with zipfile.ZipFile(path, "w") as zf:
        good_path = save_easyfind(tmp_path / "base.easyfind", doc)
    with zipfile.ZipFile(good_path, "r") as src, zipfile.ZipFile(path, "w") as dst:
        for name in src.namelist():
            dst.writestr(name, src.read(name))
        dst.writestr(blob_path, blob)
    previews = json.loads(zipfile.ZipFile(path).read(PREVIEWS_INDEX_PATH))
    previews["previews"][0]["sha256"] = wrong_hash
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr(PREVIEWS_INDEX_PATH, dumps_json(previews))
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("hash mismatch" in e for e in report.errors)


def test_validation_node_missing_asset(tmp_path):
    path = _valid_file(tmp_path)
    with zipfile.ZipFile(path, "r") as zf:
        nodes = zf.read(NODES_PATH).decode("utf-8")
    bad_node = json.loads(nodes.strip())
    bad_node["asset_refs"] = [{"asset_id": "missing", "sub_id": None, "role": "primary"}]
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr(NODES_PATH, json.dumps(bad_node) + "\n")
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("references missing asset" in e for e in report.errors)


def test_validation_asset_tag_missing_node(tmp_path):
    path = _valid_file(tmp_path)
    tags = {"tags": [{"node_id": "asset:missing", "location_id": None, "tags": [], "favorite": False, "note_id": None, "metadata": {}}]}
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr(ASSET_TAGS_PATH, dumps_json(tags))
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("Asset tag references missing node" in e for e in report.errors)


def test_validation_manual_merge_missing_node(tmp_path):
    path = _valid_file(tmp_path)
    merges = {"merges": [{"merge_id": "m1", "label": "x", "member_node_ids": ["asset:ghost"], "created_utc": "t", "reason": "r", "metadata": {}}]}
    with zipfile.ZipFile(path, "a") as zf:
        zf.writestr(MANUAL_MERGES_PATH, dumps_json(merges))
    report = validate_easyfind(path)
    assert report.ok is False
    assert any("Manual merge" in e and "missing node" in e for e in report.errors)


def test_read_preview_unknown_id(tmp_path):
    path = _valid_file(tmp_path)
    try:
        read_easyfind_preview(path, "does-not-exist")
        raised = False
    except EasyFindError as exc:
        raised = True
        assert "Unknown preview ID" in str(exc)
    assert raised


def test_atomic_write_safety(tmp_path):
    path = _valid_file(tmp_path)
    original = path.read_bytes()
    doc = finalize_easyfind_document(
        create_easyfind_document(assets=[_asset(), _asset("a2")], rom_path="game.nds"),
    )

    def fail_hook():
        raise OSError("Simulated write failure")

    _set_write_failure_hook(fail_hook)
    try:
        try:
            save_easyfind(path, doc)
            raised = False
        except OSError as exc:
            raised = True
            assert "Simulated write failure" in str(exc)
        assert raised
    finally:
        _set_write_failure_hook(None)

    assert path.read_bytes() == original
    report = validate_easyfind(path)
    assert report.ok is True
