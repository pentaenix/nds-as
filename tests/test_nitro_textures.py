from dsm.nitro_textures import (
    PaletteEntry,
    Tex0Info,
    TextureEntry,
    attempt_decode_texture,
    decode_btx_images,
    decode_guided_tex0_images,
    score_tex0_candidate,
    texture_data_requirements,
    validate_texture_ranges,
)
from test_decoders import make_btx0_4bpp


def _texture(fmt: int, w_pow: int, h_pow: int, offset: int, name: str = "tex") -> TextureEntry:
    tex_params = (offset >> 3) | (w_pow << 20) | (h_pow << 23) | (fmt << 26)
    return TextureEntry(name, tex_params, 0)


def test_texture_data_requirements_counts():
    tex = _texture(1, 0, 0, 0, "a3i5")  # 8x8
    assert texture_data_requirements(tex) == {"block1": 64, "palette_colors": 32}
    tex = _texture(2, 0, 0, 0, "2bpp")
    assert texture_data_requirements(tex) == {"block1": 16, "palette_colors": 4}
    tex = _texture(3, 0, 0, 0, "4bpp")
    assert texture_data_requirements(tex) == {"block1": 32, "palette_colors": 16}
    tex = _texture(4, 0, 0, 0, "8bpp")
    assert texture_data_requirements(tex) == {"block1": 64, "palette_colors": 256}
    tex = _texture(5, 1, 1, 0, "4x4")  # 16x16
    req = texture_data_requirements(tex)
    assert req["block2"] == 16 * 4
    assert req["block3"] == 16 * 2
    assert req["palette_colors"] == 4
    tex = _texture(6, 0, 0, 0, "a5i3")
    assert texture_data_requirements(tex) == {"block1": 64, "palette_colors": 8}
    tex = _texture(7, 0, 0, 0, "direct")
    assert texture_data_requirements(tex) == {"block1": 128, "palette_colors": 0}


def test_validate_texture_ranges_catches_short_blocks():
    tex = _texture(4, 0, 0, 0)
    pal = PaletteEntry("pal", 0, 0)
    tex0 = Tex0Info(block1=b"\x00" * 10, block2=b"", block3=b"", block4=b"\x00" * 4, textures=[tex], palettes=[pal])
    problems = validate_texture_ranges(tex, pal, tex0)
    assert any("block1 out of range" in p for p in problems)

    tex5 = _texture(5, 1, 1, 0)
    tex0_5 = Tex0Info(block1=b"", block2=b"\x00" * 8, block3=b"\x00" * 4, block4=b"\x00" * 8, textures=[tex5], palettes=[pal])
    problems = validate_texture_ranges(tex5, pal, tex0_5)
    assert any("block2 out of range" in p or "block3 out of range" in p for p in problems)

    tex0_pal = Tex0Info(block1=b"\x00" * 64, block2=b"", block3=b"", block4=b"\x00" * 4, textures=[tex], palettes=[pal])
    problems = validate_texture_ranges(tex, pal, tex0_pal)
    assert any("palette out of range" in p for p in problems)

    problems = validate_texture_ranges(tex, None, tex0_pal)
    assert any("missing palette" in p for p in problems)


def test_score_tex0_candidate_prefers_requested_names():
    images_a = [type("Img", (), {"name": "other", "palette_name": None})()]
    images_b = [
        type("Img", (), {"name": "wanted_a", "palette_name": "pal"})(),
        type("Img", (), {"name": "wanted_b", "palette_name": "pal"})(),
        type("Img", (), {"name": "wanted_c", "palette_name": "pal"})(),
    ]
    tex0 = Tex0Info(block1=b"", block2=b"", block3=b"", block4=b"", textures=[], palettes=[])
    score_a = score_tex0_candidate(tex0, images_a, requested_names={"wanted_a", "wanted_b"})
    score_b = score_tex0_candidate(tex0, images_b, requested_names={"wanted_a", "wanted_b"})
    assert score_b > score_a


def test_decode_btx_images_still_decodes_synthetic_archive():
    images = decode_btx_images(make_btx0_4bpp(), mode="resolved")
    assert images
    assert images[0].name == "boat_tex"


def test_guided_decode_finds_requested_texture_name():
    blob = make_btx0_4bpp()
    images = decode_guided_tex0_images(blob, texture_requests=[("boat_tex", None)])
    assert images
    assert images[0].name == "boat_tex"


def test_attempt_decode_texture_reports_missing_palette():
    tex = _texture(3, 0, 0, 0, "indexed")
    tex0 = Tex0Info(block1=b"\x00" * 64, block2=b"", block3=b"", block4=b"\x00" * 32, textures=[tex], palettes=[])
    decoded, problems = attempt_decode_texture(tex, None, tex0)
    assert decoded is None
    assert any("missing palette" in p for p in problems)
