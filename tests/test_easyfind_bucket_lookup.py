import zipfile

import pytest

from rae.easyfind import (
    create_easyfind_document,
    filter_nodes,
    load_easyfind,
    load_node_index,
    save_easyfind,
)
from rae.easyfind.canvas_filters import EasyFindCanvasFilters
from rae.easyfind.format import BUCKET_LOOKUP_PATH
from rae.easyfind.models import EasyFindColorSignature
from rae.easyfind.store import EasyFindValidationError
from rae.easyfind.validation import EasyFindCorruptError
from rae.scanner import Asset
from tests.easyfind_testutil import finalize_easyfind_document


def _asset(asset_id: str, magic: str = "BTX0") -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=f"tex/{asset_id}.btx",
        kind=magic,
        magic=magic,
        extension=".bin",
        data=b"x" * 8,
        original_data=b"x" * 8,
    )


def _document_with_buckets():
    assets = [_asset(f"a{i}") for i in range(6)]
    doc = create_easyfind_document(assets=assets, rom_path="game.nds")
    for index, node in enumerate(doc.nodes):
        bucket = "red" if index < 2 else "blue"
        doc.color_signatures.append(
            EasyFindColorSignature(
                signature_id=f"sig:{node.node_id}",
                node_id=node.node_id,
                dominant_bucket=bucket,
                dominant_colors=["#ff0000" if bucket == "red" else "#0000ff"],
                secondary_buckets=["cyan"] if bucket == "blue" and index == 2 else [],
                brightness="mid",
                saturation="high",
                transparent_ratio=0.0,
                metadata={},
            )
        )
        node.color_signature_ref = f"sig:{node.node_id}"
    return finalize_easyfind_document(doc)


def test_save_requires_bucket_lookup(tmp_path):
    doc = create_easyfind_document(assets=[_asset("a0")], rom_path="game.nds")
    with pytest.raises(EasyFindValidationError):
        save_easyfind(tmp_path / "bad.easyfind", doc)


def test_save_writes_bucket_lookup_file(tmp_path):
    doc = _document_with_buckets()
    path = save_easyfind(tmp_path / "lookup.easyfind", doc)
    with zipfile.ZipFile(path, "r") as zf:
        assert BUCKET_LOOKUP_PATH in zf.namelist()


def test_load_requires_bucket_lookup_file(tmp_path):
    doc = finalize_easyfind_document(
        create_easyfind_document(assets=[_asset("a0")], rom_path="game.nds"),
    )
    path = save_easyfind(tmp_path / "good.easyfind", doc)
    with zipfile.ZipFile(path, "r") as zf:
        names = [name for name in zf.namelist() if name != BUCKET_LOOKUP_PATH]
        broken = tmp_path / "broken.easyfind"
        with zipfile.ZipFile(broken, "w") as out:
            for name in names:
                out.writestr(name, zf.read(name))
    with pytest.raises(EasyFindCorruptError):
        load_easyfind(broken)


def test_filter_uses_persisted_lookup(tmp_path):
    doc = _document_with_buckets()
    path = save_easyfind(tmp_path / "lookup.easyfind", doc)
    loaded = load_easyfind(path)
    index = load_node_index(loaded)

    red_nodes = filter_nodes(
        loaded,
        EasyFindCanvasFilters(
            filter_mode="focus",
            focus_primary_colors=frozenset({"red"}),
        ),
        index=index,
    )
    assert len(red_nodes) == 2
