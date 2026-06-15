from rae.core.preview_sheet import (
    asset_supports_sheet_preview,
    safe_sheet_entry_filename,
    sheet_preview_is_active,
)


class _Asset:
    def __init__(self, asset_id: str, magic: str) -> None:
        self.asset_id = asset_id
        self.magic = magic


def test_asset_supports_sheet_preview() -> None:
    assert asset_supports_sheet_preview(_Asset("a", "BTX0"))
    assert asset_supports_sheet_preview(_Asset("a", "RGCN"))
    assert not asset_supports_sheet_preview(_Asset("a", "BMD0"))


def test_sheet_preview_is_active_requires_matching_asset_and_entries() -> None:
    asset = _Asset("a1", "BTX0")
    entries = [{"key": "tex1", "path": "/tmp/tex1.png"}, {"key": "tex2", "path": "/tmp/tex2.png"}]
    assert sheet_preview_is_active(asset, sheet_asset_id="a1", sheet_entries=entries)
    assert not sheet_preview_is_active(asset, sheet_asset_id="a2", sheet_entries=entries)
    assert not sheet_preview_is_active(asset, sheet_asset_id="a1", sheet_entries=entries[:1])
    assert not sheet_preview_is_active(_Asset("a1", "BMD0"), sheet_asset_id="a1", sheet_entries=entries)


def test_safe_sheet_entry_filename() -> None:
    assert safe_sheet_entry_filename("kk_tourou_b_1") == "kk_tourou_b_1"
    assert safe_sheet_entry_filename("weird name!") == "weird_name_"
