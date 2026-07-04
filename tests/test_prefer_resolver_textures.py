"""Tests for resolver-vs-apicula texture map precedence."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from rae.platforms.nds.gltf.preview_textures import prefer_resolver_texture_map
from rae.model_texture_resolver import ModelTextureResolution
from rae.platforms.nds.model_module.preview_pipeline import _conversion_siblings
from rae.scanner import Asset
from test_decoders import make_btx0_4bpp


def _asset(asset_id: str, path: str, magic: str, data: bytes) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind="Model" if magic == "BMD0" else "Texture archive",
        magic=magic,
        extension=".nsbmd" if magic == "BMD0" else ".nsbtx",
        data=data,
        original_data=data,
    )


def test_prefer_resolver_texture_map_overrides_apicula(tmp_path: Path) -> None:
    wrong = tmp_path / "batt_field04d_1.png"
    right = tmp_path / "resolved_batt_field04d_1.png"
    Image.new("RGBA", (64, 64), (255, 0, 0, 255)).save(wrong)
    Image.new("RGBA", (32, 32), (0, 255, 0, 255)).save(right)

    apicula_map = {"batt_field04d_1": wrong}
    resolver_map = {"batt_field04d_1": right}
    merged = prefer_resolver_texture_map(resolver_map, apicula_map)
    assert merged["batt_field04d_1"] == right


def test_conversion_siblings_excludes_unresolved_folder_btx() -> None:
    model = _asset("m", "a/0/1/1/file_0089.bin.nsbmd", "BMD0", b"BMD0")
    wrong_tex = _asset("t1", "a/0/1/1/file_0001.bin.nsbtx", "BTX0", make_btx0_4bpp())
    good_tex = _asset("t2", "a/0/1/1/file_0089.bin.nsbtx", "BTX0", make_btx0_4bpp())
    anim = _asset("a1", "a/0/1/1/file_0090.bin.nsbca", "BCA0", b"BCA0")
    resolution = ModelTextureResolution("textured_verified", None, resolved_assets=[good_tex])
    all_assets = [model, wrong_tex, good_tex, anim]
    siblings = _conversion_siblings(model, all_assets, resolution)
    assert good_tex in siblings
    assert wrong_tex not in siblings
    assert anim in siblings
