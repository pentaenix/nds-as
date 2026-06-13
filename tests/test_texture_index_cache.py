from pathlib import Path

from rae.scanner import Asset
from rae.texture_index import (
    TextureIndexContext,
    load_texture_index_cache,
    save_texture_index_cache,
    texture_index_path_for_game_code,
)
from rae.texture_library import TextureLibrary, TextureLibraryStore
from test_decoders import make_btx0_4bpp


def asset(asset_id: str, path: str, magic: str, data: bytes) -> Asset:
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind="Model" if magic == "BMD0" else "Texture archive",
        magic=magic,
        extension=".nsbmd" if magic == "BMD0" else ".nsbtx",
        data=data,
        original_data=data,
    )


def test_texture_index_cache_roundtrip(tmp_path: Path):
    tex = asset("tex1", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    context = TextureIndexContext(game_code="ABCD", scan_mode="fast", cache_root=tmp_path)
    store = TextureLibraryStore()
    store.set_context(game_code="ABCD", scan_mode="fast", cache_root=tmp_path)
    library = store.get_or_build([tex])

    saved = save_texture_index_cache(
        context,
        [tex],
        library.manifests,
        manifest_cache_version=2,
        root=tmp_path,
    )
    assert saved is not None
    assert saved == texture_index_path_for_game_code("ABCD", root=tmp_path)

    loaded = load_texture_index_cache(
        context,
        [tex],
        manifest_cache_version=2,
        root=tmp_path,
    )
    assert loaded is not None
    cached_library = TextureLibrary.from_manifests([tex], loaded)
    assert cached_library.find_exact("boat_tex")


def test_texture_index_cache_invalidates_on_rom_hash(tmp_path: Path):
    tex = asset("tex1", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    rom_a = tmp_path / "game_a.nds"
    rom_b = tmp_path / "game_b.nds"
    rom_a.write_bytes(b"ROM-A")
    rom_b.write_bytes(b"ROM-B")

    context_a = TextureIndexContext(game_code="WXYZ", rom_path=str(rom_a), scan_mode="fast")
    store = TextureLibraryStore()
    store.set_context(game_code="WXYZ", rom_path=str(rom_a), scan_mode="fast")
    library = store.get_or_build([tex])
    save_texture_index_cache(context_a, [tex], library.manifests, manifest_cache_version=2, root=tmp_path)

    context_b = TextureIndexContext(game_code="WXYZ", rom_path=str(rom_b), scan_mode="fast")
    assert load_texture_index_cache(context_b, [tex], manifest_cache_version=2, root=tmp_path) is None


def test_texture_index_cache_invalidates_on_scan_mode(tmp_path: Path):
    tex = asset("tex1", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    context_fast = TextureIndexContext(game_code="PQRS", scan_mode="fast")
    store = TextureLibraryStore()
    store.set_context(game_code="PQRS", scan_mode="fast")
    library = store.get_or_build([tex])
    save_texture_index_cache(context_fast, [tex], library.manifests, manifest_cache_version=2, root=tmp_path)

    context_deep = TextureIndexContext(game_code="PQRS", scan_mode="deep")
    assert load_texture_index_cache(context_deep, [tex], manifest_cache_version=2, root=tmp_path) is None


def test_texture_index_cache_keeps_separate_files_per_game_code(tmp_path: Path):
    tex = asset("tex1", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    for code in ("AAAA", "BBBB"):
        context = TextureIndexContext(game_code=code, scan_mode="fast")
        store = TextureLibraryStore()
        store.set_context(game_code=code, scan_mode="fast")
        library = store.get_or_build([tex])
        save_texture_index_cache(context, [tex], library.manifests, manifest_cache_version=2, root=tmp_path)

    assert texture_index_path_for_game_code("AAAA", root=tmp_path).is_file()
    assert texture_index_path_for_game_code("BBBB", root=tmp_path).is_file()


def test_texture_library_store_loads_from_disk_cache(tmp_path: Path):
    tex = asset("tex1", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    context = TextureIndexContext(game_code="TEST", scan_mode="fast", cache_root=tmp_path)

    warm = TextureLibraryStore()
    warm.set_context(game_code="TEST", scan_mode="fast", cache_root=tmp_path)
    library = warm.get_or_build([tex])
    save_texture_index_cache(context, [tex], library.manifests, manifest_cache_version=2, root=tmp_path)

    cold = TextureLibraryStore()
    cold.set_context(game_code="TEST", scan_mode="fast", cache_root=tmp_path)
    loaded = cold.get_or_build([tex])
    assert loaded.find_exact("boat_tex")
