from rae.scanner import Asset
from rae.session import save_session_zip, load_session_zip


def test_session_round_trip(tmp_path):
    assets = [
        Asset(
            asset_id="a1",
            virtual_path="a/0/0/8/file_0000.bin.nsbmd",
            kind="Nitro 3D model",
            magic="BMD0",
            extension=".nsbmd",
            data=b"BMD0demo",
            original_data=b"BMD0demo",
            mapping_category="models",
            mapping_label="Map models",
        ),
        Asset(
            asset_id="a2",
            virtual_path="a/0/1/4/file_0000.bin.nsbtx",
            kind="Nitro texture archive",
            magic="BTX0",
            extension=".nsbtx",
            data=b"BTX0demo",
            original_data=b"rawBTX0demo",
            compressed=True,
            mapping_category="textures",
        ),
    ]
    path = save_session_zip(
        tmp_path / "work.dsmsession",
        assets=assets,
        rom_path="roms/game.nds",
        profile_text="profile",
        mapping_id="pokemon_bw2",
    )
    loaded = load_session_zip(path)
    loaded_assets = loaded["assets"]
    assert len(loaded_assets) == 2
    assert loaded_assets[0].data == b"BMD0demo"
    assert loaded_assets[1].original_data == b"rawBTX0demo"
    assert loaded["manifest"]["mapping_id"] == "pokemon_bw2"
