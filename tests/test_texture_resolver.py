from rae.model_texture_resolver import resolve_model_textures
from rae.scanner import Asset
from rae.texture_library import TextureLibrary, TextureLibraryStore
from test_decoders import make_btx0_4bpp


def asset(asset_id, path, magic, data):
    return Asset(
        asset_id=asset_id,
        virtual_path=path,
        kind="Model" if magic == "BMD0" else "Texture archive",
        magic=magic,
        extension=".nsbmd" if magic == "BMD0" else ".nsbtx",
        data=data,
        original_data=data,
    )


def test_texture_library_lists_exact_btx_texture_name():
    tex = asset("tex", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    lib = TextureLibrary.from_assets([tex])
    matches = lib.find_exact("boat_tex")
    assert len(matches) == 1
    assert matches[0].texture_asset_id == "tex"
    assert lib.decode_binding(matches[0]) is not None


def test_model_resolver_uses_exact_texture_dictionary_match():
    model_data = b"BMD0" + b"\0" * 32 + b"boat_tex\0".ljust(16, b"\0")
    model = asset("model", "models/boat.nsbmd", "BMD0", model_data)
    tex = asset("tex", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    resolution = resolve_model_textures(model, [model, tex])
    assert resolution.status == "textured_verified"
    assert resolution.resolved_assets[0].asset_id == "tex"
    assert resolution.decoded_images


def test_model_resolver_does_not_pin_without_exact_texture_match():
    model_data = b"BMD0" + b"\0" * 32 + b"missing_tex\0".ljust(16, b"\0")
    model = asset("model", "models/missing.nsbmd", "BMD0", model_data)
    tex = asset("tex", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    resolution = resolve_model_textures(model, [model, tex])
    assert resolution.status == "unresolved"
    assert not resolution.resolved_assets


def test_model_resolver_decodes_embedded_tex0_without_external_candidate():
    btx = make_btx0_4bpp()
    tex0 = btx[0x14:]
    model_data = b"BMD0" + b"\0" * 32 + tex0
    model = asset("model", "models/embedded.nsbmd", "BMD0", model_data)
    resolution = resolve_model_textures(model, [model])
    assert resolution.status == "embedded_texture"
    assert resolution.decoded_images
    assert resolution.resolved_assets[0].asset_id == "model"


def test_texture_library_store_reuses_index():
    tex = asset("tex", "textures/boat.nsbtx", "BTX0", make_btx0_4bpp())
    store = TextureLibraryStore()
    first = store.get_or_build([tex])
    second = store.get_or_build([tex])
    assert first is second
    assert store.is_ready_for([tex])


def test_model_resolver_uses_material_name_when_texture_name_missing():
    btx = make_btx0_4bpp()
    model_data = b"BMD0" + b"\0" * 32 + b"boat_tex\0".ljust(16, b"\0")
    model = asset("model", "models/boat.nsbmd", "BMD0", model_data)
    tex = asset("tex", "textures/boat.nsbtx", "BTX0", btx)
    resolution = resolve_model_textures(model, [model, tex])
    assert resolution.decoded_images
    assert resolution.status in {"textured_verified", "exact_match_unverified"}


def test_guided_tex0_decode_accepts_lightmap_palette_suffix():
    from rae.nitro_textures import decode_guided_tex0_images, make_contact_sheet  # noqa: F401
    from test_decoders import namelist
    import struct

    tex_params = (0 << 0) | (0 << 20) | (0 << 23) | (3 << 26)
    texels = bytes(((i % 16) | (((i + 1) % 16) << 4)) for i in range(0, 64, 2))
    palette = b"".join(struct.pack("<H", i | (i << 5) | (i << 10)) for i in range(16))
    textures = namelist([("flower02_lm1", struct.pack("<II", tex_params, 0))], 8)
    palettes = namelist([("flower02_pl", struct.pack("<HH", 0, 0))], 4)
    header_len = 0x38
    textures_off = header_len
    palettes_off = textures_off + len(textures)
    block1_off = palettes_off + len(palettes)
    block4_off = block1_off + len(texels)
    tex0 = bytearray(b"TEX0" + b"\0" * (header_len - 4))
    struct.pack_into("<H", tex0, 0x0C, len(texels) >> 3)
    struct.pack_into("<H", tex0, 0x0E, textures_off)
    struct.pack_into("<I", tex0, 0x14, block1_off)
    struct.pack_into("<H", tex0, 0x18, 0)
    struct.pack_into("<I", tex0, 0x20, 0)
    struct.pack_into("<I", tex0, 0x24, 0)
    struct.pack_into("<H", tex0, 0x2C, len(palette) >> 3)
    struct.pack_into("<I", tex0, 0x30, palettes_off)
    struct.pack_into("<I", tex0, 0x34, block4_off)
    blob = bytes(tex0) + textures + palettes + texels + palette
    images = decode_guided_tex0_images(
        blob,
        texture_requests=[("flower02_lm1", None)],
    )
    assert images
    assert images[0].name == "flower02_lm1"


def test_index_paired_palette_decode_when_names_differ():
    from rae.nitro_textures import decode_btx_images
    from test_decoders import namelist
    import struct

    tex_params = (0 << 0) | (0 << 20) | (0 << 23) | (3 << 26)
    texels = bytes(((i % 16) | (((i + 1) % 16) << 4)) for i in range(0, 64, 2))
    palette = b"".join(struct.pack("<H", i | (i << 5) | (i << 10)) for i in range(16))
    textures = namelist([("flower_lm1", struct.pack("<II", tex_params, 0))], 8)
    palettes = namelist([("flower_pl", struct.pack("<HH", 0, 0))], 4)
    header_len = 0x38
    textures_off = header_len
    palettes_off = textures_off + len(textures)
    block1_off = palettes_off + len(palettes)
    block4_off = block1_off + len(texels)
    tex0 = bytearray(b"TEX0" + b"\0" * (header_len - 4))
    struct.pack_into("<H", tex0, 0x0C, len(texels) >> 3)
    struct.pack_into("<H", tex0, 0x0E, textures_off)
    struct.pack_into("<I", tex0, 0x14, block1_off)
    struct.pack_into("<H", tex0, 0x18, 0)
    struct.pack_into("<I", tex0, 0x20, 0)
    struct.pack_into("<I", tex0, 0x24, 0)
    struct.pack_into("<H", tex0, 0x2C, len(palette) >> 3)
    struct.pack_into("<I", tex0, 0x30, palettes_off)
    struct.pack_into("<I", tex0, 0x34, block4_off)
    blob = bytes(tex0) + textures + palettes + texels + palette
    images = decode_btx_images(blob, mode="resolved")
    assert images
    assert images[0].name == "flower_lm1"


def test_deferred_resolver_skips_library_for_embedded_model():
    btx = make_btx0_4bpp()
    tex0 = btx[0x14:]
    model_data = b"BMD0" + b"\0" * 32 + tex0
    model = asset("model", "models/embedded.nsbmd", "BMD0", model_data)
    resolution = resolve_model_textures(model, [model], defer_library_build=True)
    assert resolution.status == "embedded_texture"
    assert resolution.decoded_images
