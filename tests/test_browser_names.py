from rae.nitro_names import asset_browser_name, asset_filename_label
from rae.scanner import Asset


def _asset(virtual_path: str, magic: str, data: bytes = b"") -> Asset:
    return Asset(
        asset_id="test",
        virtual_path=virtual_path,
        kind="test",
        magic=magic,
        extension=".bin",
        data=data,
        original_data=data,
    )


def test_asset_filename_label():
    assert asset_filename_label("a/0/3/9/file_0000.bin.nsbtx") == "file_0000.bin.nsbtx"


def test_asset_browser_name_falls_back_to_filename():
    asset = _asset("a/0/3/9/file_0000.bin.nsbtx", "BTX0", b"not a real btx")
    assert asset_browser_name(asset) == "file_0000.bin.nsbtx"


def test_asset_browser_name_uses_dictionary_name():
    from test_decoders import make_btx0_4bpp

    asset = _asset("a/0/3/9/file_0000.bin.nsbtx", "BTX0", make_btx0_4bpp())
    assert asset_browser_name(asset) == "boat_tex"
