from __future__ import annotations

from rae.core.modules import PlatformDispatch, asset_platform_id, get_platform_modules
from rae.core.modules.types import PreviewRoute
from rae.scanner import Asset


def _asset(magic: str) -> Asset:
    return Asset(
        asset_id="t",
        virtual_path="test",
        kind="test",
        magic=magic,
        extension=".bin",
        data=b"",
        original_data=b"",
    )


def test_asset_platform_routing():
    assert asset_platform_id(_asset("BMD0")) == "nds"
    assert asset_platform_id(_asset("ABA")) == "mobile"
    assert asset_platform_id(_asset("HOME")) == "mobile"


def test_preview_route_nds_model():
    assert PlatformDispatch.preview_route(_asset("BMD0"), rom_platform_id="nds") == PreviewRoute.NDS_MODEL


def test_preview_route_mobile_model():
    assert PlatformDispatch.preview_route(_asset("ABA"), rom_platform_id="mobile") == PreviewRoute.MOBILE_MODEL


def test_platform_modules_independent():
    nds = get_platform_modules("nds")
    mobile = get_platform_modules("mobile")
    assert nds.scan.platform_id == "nds"
    assert mobile.scan.platform_id == "mobile"
    assert nds.model is not mobile.model
