"""EasyFind preview bake policy tests."""
from __future__ import annotations

from rae.easyfind.build_index import asset_to_node, create_easyfind_document
from rae.easyfind.build_previews import enrich_document_with_previews
from rae.easyfind.preview_policy import preview_skipped_reason, should_bake_preview
from rae.scanner import Asset


def _asset(asset_id: str, magic: str, **kwargs) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=f"path/{asset_id}.bin",
        kind=magic,
        magic=magic,
        extension=".bin",
        data=b"x" * 16,
        original_data=b"x" * 16,
        **kwargs,
    )


def test_skips_audio_and_animation():
    audio = _asset("a1", "SDAT")
    audio_node = asset_to_node(audio)
    assert audio_node.node_kind == "audio"
    assert should_bake_preview(audio_node, audio) is False

    anim = _asset("n1", "BCA0")
    anim_node = asset_to_node(anim)
    assert anim_node.node_kind == "animation"
    assert should_bake_preview(anim_node, anim) is False


def test_never_skips_unknown():
    asset = _asset("u1", "ZZZZ")
    node = asset_to_node(asset)
    assert node.node_kind == "unknown"
    assert should_bake_preview(node, asset) is True


def test_skips_palette_magic():
    asset = _asset("p1", "RLCN")
    node = asset_to_node(asset)
    assert should_bake_preview(node, asset) is False


def test_enrich_skips_audio_previews():
    assets = [
        _asset("m1", "BMD0"),
        _asset("a1", "SDAT"),
    ]
    doc = create_easyfind_document(assets=assets, rom_path="game.nds")
    enriched, blobs = enrich_document_with_previews(doc, assets)
    audio_node = next(n for n in enriched.nodes if n.node_kind == "audio")
    assert audio_node.preview_ref is None
    assert len(blobs) == 0
