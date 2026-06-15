from rae.easyfind import create_easyfind_document, match_easyfind_assets
from rae.easyfind.build_index import asset_to_ref
from rae.scanner import Asset


def _asset(
    asset_id: str,
    path: str,
    magic: str = "BMD0",
    data: bytes = b"x" * 8,
    **kwargs,
) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind="Model",
        magic=magic,
        extension=".nsbmd",
        data=data,
        original_data=data,
        **kwargs,
    )


def test_match_exact_asset_id():
    current = [_asset("a1", "path/a.nsbmd")]
    doc = create_easyfind_document(assets=current)
    report = match_easyfind_assets(current, doc.assets)
    assert report.matched["a1"] == "a1"
    assert report.missing == []
    assert report.ambiguous == {}


def test_match_virtual_path_magic_size():
    ef_asset = asset_to_ref(_asset("old_id", "path/a.nsbmd", data=b"12345678"))
    current = [_asset("new_id", "path/a.nsbmd", data=b"12345678")]
    report = match_easyfind_assets(current, [ef_asset])
    assert report.matched["old_id"] == "new_id"


def test_match_rom_offset_magic_size():
    ef_asset = asset_to_ref(_asset(
        "old_id", "other/path.nsbmd",
        rom_file_id=3, rom_offset=1024, data=b"12345678",
    ))
    current = [_asset(
        "new_id", "path/a.nsbmd",
        rom_file_id=3, rom_offset=1024, data=b"12345678",
    )]
    report = match_easyfind_assets(current, [ef_asset])
    assert report.matched["old_id"] == "new_id"


def test_match_missing_asset():
    ef_asset = asset_to_ref(_asset("gone", "path/missing.nsbmd"))
    report = match_easyfind_assets([], [ef_asset])
    assert report.missing == ["gone"]
    assert report.matched == {}


def test_match_ambiguous_weak():
    ef_asset = asset_to_ref(_asset("ef1", "path/a.nsbmd", magic="BMD0", data=b"aaaaaaaa"))
    current = [
        _asset("c1", "path/a.nsbmd", magic="BMD0", data=b"bbbbbbbb"),
        _asset("c2", "path/a.nsbmd", magic="BMD0", data=b"cccccccc"),
    ]
    report = match_easyfind_assets(current, [ef_asset])
    assert "ef1" in report.ambiguous
    assert len(report.ambiguous["ef1"]) == 2
