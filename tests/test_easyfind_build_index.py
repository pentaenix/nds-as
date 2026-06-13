from rae.easyfind import classify_asset_magic, create_easyfind_document
from rae.scanner import Asset


def _asset(
    asset_id: str,
    path: str,
    magic: str,
    data: bytes = b"demo",
    **kwargs,
) -> Asset:
    ext_map = {
        "BMD0": ".nsbmd",
        "BTX0": ".nsbtx",
        "RECN": ".ncer",
        "RLCN": ".nclr",
        "SDAT": ".sdat",
        "BCA0": ".nsbca",
        "ZZZZ": ".bin",
        "NARC": ".narc",
    }
    kind_map = {
        "BMD0": "Model",
        "BTX0": "Texture archive",
        "RECN": "Sprite cells",
        "RLCN": "Palette",
        "SDAT": "Audio archive",
        "BCA0": "Animation",
        "ZZZZ": "Unknown",
        "NARC": "Archive",
    }
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind=kind_map.get(magic, "Unknown"),
        magic=magic,
        extension=ext_map.get(magic, ".bin"),
        data=data,
        original_data=kwargs.pop("original_data", data),
        **kwargs,
    )


def test_classify_asset_magic():
    assert classify_asset_magic("BMD0") == "model"
    assert classify_asset_magic("BTX0") == "texture_archive"
    assert classify_asset_magic("RECN") == "image_or_sprite_source"
    assert classify_asset_magic("SDAT") == "audio"
    assert classify_asset_magic("BCA0") == "animation"
    assert classify_asset_magic("NARC") == "archive"
    assert classify_asset_magic("ZZZZ") == "unknown"


def test_create_easyfind_document_from_assets():
    assets = [
        _asset("m1", "a/0/0/1/model.nsbmd", "BMD0"),
        _asset("t1", "a/0/0/2/tex.nsbtx", "BTX0"),
        _asset("s1", "a/0/0/3/sprite.ncer", "RECN"),
        _asset("p1", "a/0/0/4/pal.nclr", "RLCN"),
        _asset("a1", "a/0/0/5/audio.sdat", "SDAT"),
        _asset("an1", "a/0/0/6/anim.nsbca", "BCA0"),
        _asset("u1", "a/0/0/7/unknown.bin", "ZZZZ"),
        _asset(
            "c1", "a/0/0/8/compressed.bin", "BTX0",
            data=b"small", original_data=b"raw" * 10, compressed=True,
        ),
        _asset(
            "cv1", "a/0/0/9/carved.bin", "BMD0",
            carved=True, carved_offset=128,
        ),
        _asset(
            "map1", "a/0/1/0/mapped.nsbmd", "BMD0",
            mapping_category="models",
            mapping_label="Town models",
            mapping_confidence="high",
        ),
    ]
    doc = create_easyfind_document(
        assets=assets,
        rom_path="roms/game.nds",
        rom_title="Test Game",
        rom_game_code="TEST",
    )

    assert len(doc.assets) == len(assets)
    assert len(doc.nodes) == len(assets) - 1  # RLCN palettes are indexed but not mapped
    assert doc.manifest.counts["assets"] == len(assets)
    assert doc.manifest.counts["nodes"] == len(assets) - 1
    assert doc.quick_open.counts["assets"] == len(assets)
    assert doc.quick_open.counts["nodes"] == len(assets) - 1

    by_id = {a.asset_id: a for a in doc.assets}
    assert by_id["m1"].identity.asset_id == "m1"
    assert by_id["m1"].identity.virtual_path == "a/0/0/1/model.nsbmd"
    assert by_id["c1"].compressed is True
    assert by_id["cv1"].carved is True
    assert by_id["cv1"].carved_offset == 128
    assert by_id["map1"].mapping_category == "models"
    assert by_id["map1"].mapping_label == "Town models"
    assert by_id["map1"].mapping_confidence == "high"

    kinds = {n.node_id.split(":", 1)[1]: n.node_kind for n in doc.nodes}
    assert kinds["m1"] == "model"
    assert kinds["t1"] == "texture_archive"
    assert kinds["s1"] == "image_or_sprite_source"
    assert "p1" not in kinds
    assert kinds["a1"] == "audio"
    assert kinds["an1"] == "animation"
    assert kinds["u1"] == "unknown"
