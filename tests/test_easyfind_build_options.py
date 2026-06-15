from rae.easyfind.build_options import BUILD_MODE_CATCHUP, BUILD_MODE_TYPES, EasyFindBuildOptions
from rae.easyfind.build_previews import _should_attempt_bake
from rae.easyfind.build_index import asset_to_node
from rae.scanner import Asset


def _model_asset() -> Asset:
    return Asset(
        asset_id="m1",
        virtual_path="chars/hero.bmd",
        kind="BMD0",
        magic="BMD0",
        extension=".bmd",
        data=b"BMD0" + b"x" * 8,
        original_data=b"BMD0" + b"x" * 8,
    )


def test_catchup_only_missing_previews():
    node = asset_to_node(_model_asset())
    asset = _model_asset()
    opts = EasyFindBuildOptions(mode=BUILD_MODE_CATCHUP)
    assert _should_attempt_bake(node, asset, options=opts, has_preview=False) is True
    assert _should_attempt_bake(node, asset, options=opts, has_preview=True) is False


def test_types_only_selected_kind():
    node = asset_to_node(_model_asset())
    asset = _model_asset()
    opts = EasyFindBuildOptions(mode=BUILD_MODE_TYPES, bake_node_kinds=frozenset({"model"}))
    assert _should_attempt_bake(node, asset, options=opts, has_preview=True) is True
    opts_sprite = EasyFindBuildOptions(mode=BUILD_MODE_TYPES, bake_node_kinds=frozenset({"image_or_sprite_source"}))
    assert _should_attempt_bake(node, asset, options=opts_sprite, has_preview=False) is False


def test_includes_model_previews_modes():
    assert EasyFindBuildOptions(mode=BUILD_MODE_CATCHUP).includes_model_previews() is True
    assert EasyFindBuildOptions(mode=BUILD_MODE_TYPES, bake_node_kinds=frozenset({"model"})).includes_model_previews() is True
    assert EasyFindBuildOptions(
        mode=BUILD_MODE_TYPES,
        bake_node_kinds=frozenset({"image_or_sprite_source"}),
    ).includes_model_previews() is False
