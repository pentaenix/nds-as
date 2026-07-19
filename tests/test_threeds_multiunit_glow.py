"""GF multi-unit glow and alpha-mask compositing for 3DS GLB export."""
from __future__ import annotations

from pathlib import Path

import pytest

from rae.platforms.threeds.gf import GfMaterial, GfTextureUnit
from rae.platforms.threeds.glb import (
    _apply_glow_black_key,
    _composite_alfa_mask_rgba,
    _gf_duplicate_unit_glow,
    _gf_emission_channel_active,
    _gf_has_alfa_mask_unit,
    _gf_single_texture_unit,
    _is_gf_glow_overlay_material,
    _material_uses_vertex_alpha,
    _resolve_material_texture_rgba,
    _should_bake_luminance_mask,
    _texture_uses_luminance_alpha,
    _uses_additive_blend,
)

pytestmark = pytest.mark.threeds

_GF_EMISSION_DEFAULT = (0, 0, 0, 255)


def test_emission_rgb_zero_alpha_255_is_not_active_without_duplicate_units():
    mat = GfMaterial(
        name="ground",
        texture_names=["ground.tga"],
        texture_units=[
            GfTextureUnit(name="ground.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        emission=_GF_EMISSION_DEFAULT,
    )
    assert not _gf_emission_channel_active(mat)
    assert not _gf_duplicate_unit_glow(mat)


def test_duplicate_unit_glow_detected_for_fire_style_materials():
    mat = GfMaterial(
        name="fire",
        texture_names=["fire.tga", "fire.tga"],
        texture_units=[
            GfTextureUnit(name="fire.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
            GfTextureUnit(name="fire.tga", unit_index=1, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        emission=_GF_EMISSION_DEFAULT,
    )
    rgba = _luminance_mask_rgba()
    assert _gf_duplicate_unit_glow(mat)
    assert _gf_emission_channel_active(mat)
    assert _uses_additive_blend(mat, rgba, source_rgba=rgba)


def test_glow_black_key_cuts_dark_opaque_pixels():
    rgba = _luminance_mask_rgba()
    mat = GfMaterial(
        name="fire",
        texture_names=["fire.tga", "fire.tga"],
        texture_units=[
            GfTextureUnit(name="fire.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
            GfTextureUnit(name="fire.tga", unit_index=1, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        emission=_GF_EMISSION_DEFAULT,
    )
    baked = _apply_glow_black_key(rgba, mat)
    assert baked[3] == 0
    assert baked[-1] == 180


def _luminance_mask_rgba(size: int = 8) -> bytes:
    """Grayscale-on-black with a solid alpha channel (GF glow-mask storage)."""
    pixels = bytearray()
    for i in range(size * size):
        lum = 0 if i < (size * size) // 3 else 180
        pixels.extend((0, 0, 0, 255) if lum == 0 else (lum, lum, lum, 255))
    return bytes(pixels)


def test_multi_unit_tev_does_not_treat_luminance_texture_as_glow():
    """Multi-unit materials (albedo + mask + normal) share glow-like textures but are not additive."""
    rgba = _luminance_mask_rgba()
    assert _texture_uses_luminance_alpha(rgba)
    mat = GfMaterial(
        name="BodyCKurumiru_LeafVco00",
        texture_names=["body.tga", "mask.tga", "normal.tga"],
        texture_units=[
            GfTextureUnit(name="body.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
            GfTextureUnit(name="mask.tga", unit_index=1, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
            GfTextureUnit(name="normal.tga", unit_index=2, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        emission=(216, 216, 216, 0),
    )
    assert not _gf_single_texture_unit(mat)
    assert not _should_bake_luminance_mask(mat, rgba)
    assert not _uses_additive_blend(mat, rgba, source_rgba=rgba)
    baked = _apply_glow_black_key(rgba, mat)
    assert baked == rgba


def test_single_unit_luminance_mask_still_glows():
    rgba = _luminance_mask_rgba()
    mat = GfMaterial(
        name="btl_G_lili_komo01",
        texture_names=["komo.tga"],
        texture_units=[
            GfTextureUnit(name="komo.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        emission=_GF_EMISSION_DEFAULT,
    )
    assert _gf_single_texture_unit(mat)
    assert _should_bake_luminance_mask(mat, rgba)
    assert _uses_additive_blend(mat, rgba, source_rgba=rgba)


def test_opaque_pica_material_drops_tev_control_vertex_alpha():
    from rae.platforms.threeds.pica_material import PicaRenderState

    mat = GfMaterial(
        name="Body",
        render_state=PicaRenderState(
            color_operation=1 << 8,
            blend_function=(1 << 16),  # source one, destination zero
            alpha_test=0,
            depth_color_mask=1 << 12,
        ),
    )
    assert not _material_uses_vertex_alpha(mat)


def test_pica_source_alpha_overlay_keeps_vertex_alpha_and_black_keys_texture():
    from rae.platforms.threeds.pica_material import PicaRenderState

    mat = GfMaterial(
        name="GlowInc00",
        texture_names=["glow.tga", "DummyTex.tga"],
        texture_units=[
            GfTextureUnit(name="glow.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
            GfTextureUnit(name="DummyTex.tga", unit_index=1, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        render_state=PicaRenderState(
            color_operation=1 << 8,
            blend_function=(6 << 16) | (1 << 20),  # source-alpha, destination one
            alpha_test=0,
            depth_color_mask=0,
        ),
    )
    rgba = bytearray()
    for i in range(64):
        rgba.extend((0, 0, 0, 255) if i < 32 else (40, 120, 220, 255))

    baked = _apply_glow_black_key(bytes(rgba), mat)
    assert _material_uses_vertex_alpha(mat)
    assert _uses_additive_blend(mat, baked, source_rgba=bytes(rgba))
    assert baked[3] == 0
    assert baked[-1] == 220


def test_alfa_mask_composite_for_city_light02():
    from rae.platforms.threeds.pica import rgba_to_png
    from rae.platforms.threeds.gltf.texture_alpha import texture_has_fully_transparent_pixels

    base = bytearray()
    mask = bytearray()
    for i in range(8 * 8):
        if i < 20:
            base.extend((0, 0, 0, 255))
            mask.extend((255, 255, 255, 255))
        else:
            base.extend((180, 140, 40, 255))
            mask.extend((0, 0, 0, 255) if i % 3 == 0 else (200, 200, 200, 255))
    base_rgba = bytes(base)
    mask_rgba = bytes(mask)

    class _Tex:
        def __init__(self, rgba: bytes, width: int, height: int) -> None:
            self.width = width
            self.height = height
            self._rgba = rgba

        def decode_rgba(self) -> bytes:
            return self._rgba

    mat = GfMaterial(
        name="btl_G_city_light02",
        texture_names=["light02.tga", "city_alfa01.tga"],
        texture_units=[
            GfTextureUnit(name="light02.tga", unit_index=0, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
            GfTextureUnit(name="city_alfa01.tga", unit_index=1, scale=(1.0, 1.0), rotation=0.0, translation=(0.0, 0.0)),
        ],
        emission=_GF_EMISSION_DEFAULT,
    )
    tex = {
        "light02.tga": _Tex(base_rgba, 8, 8),
        "city_alfa01.tga": _Tex(mask_rgba, 8, 8),
    }
    assert _gf_has_alfa_mask_unit(mat)
    assert _is_gf_glow_overlay_material(mat)
    resolved = _composite_alfa_mask_rgba(mat, tex)
    assert resolved is not None
    rgba, width, height = resolved
    png = rgba_to_png(rgba, width, height)
    assert texture_has_fully_transparent_pixels(png)
    assert _uses_additive_blend(mat, rgba, source_rgba=base_rgba)


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0006_city_lights_export_with_alpha(tmp_path):
    from rae.platforms.threeds.gltf.apply import apply_glb_policy
    from rae.platforms.threeds.gltf.glb_io import material_texture_bytes, read_glb
    from rae.platforms.threeds.gltf.texture_alpha import texture_has_fully_transparent_pixels
    from rae.platforms.threeds.service import build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 6,
        "name": "Battle background 0006",
    }
    build_model_glb(descriptor, tmp_path)
    glb_path = tmp_path / "w_a_0_8_1_0006.glb"
    apply_glb_policy(glb_path)
    glb = read_glb(glb_path)
    materials = {m["name"]: m for m in glb.json["materials"]}

    light01 = materials["btl_G_city_light01"]
    assert light01["extras"]["rae"]["renderClass"] == "additive"

    light02 = materials["btl_G_city_light02"]
    assert light02["extras"]["rae"]["renderClass"] == "additive"
    light02_png = material_texture_bytes(glb, glb.json["materials"].index(light02))
    assert light02_png is not None
    assert texture_has_fully_transparent_pixels(light02_png)

    stage_like = materials["btl_G_city_bld00"]
    assert stage_like["extras"]["rae"]["renderClass"] in {"mask", "blend"}


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0008_sand_floor_stays_opaque(tmp_path):
    from rae.platforms.threeds.gltf.apply import apply_glb_policy
    from rae.platforms.threeds.gltf.glb_io import material_texture_bytes, read_glb
    from rae.platforms.threeds.gltf.texture_alpha import texture_has_fully_transparent_pixels
    from rae.platforms.threeds.service import build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 8,
        "name": "Battle background 0008",
    }
    build_model_glb(descriptor, tmp_path)
    glb_path = tmp_path / "w_a_0_8_1_0008.glb"
    apply_glb_policy(glb_path)
    glb = read_glb(glb_path)
    materials = {m["name"]: m for m in glb.json["materials"]}

    sand = materials["btl_G_hama_jime01"]
    assert sand["extras"]["rae"]["renderClass"] == "opaque"
    sand_png = material_texture_bytes(glb, glb.json["materials"].index(sand))
    assert sand_png is not None
    assert not texture_has_fully_transparent_pixels(sand_png)

    grass = materials["btl_G_kusa_kusa01"]
    assert grass["extras"]["rae"]["renderClass"] != "additive"

    wave = materials["btl_G_hama_Unami05"]
    assert wave["extras"]["rae"]["renderClass"] == "additive"

    sea = materials["btl_A_sea_iro045"]
    assert sea["extras"]["rae"]["renderClass"] == "blend"
    assert sea.get("alphaMode") == "BLEND"
    sea_png = material_texture_bytes(glb, glb.json["materials"].index(sea))
    assert sea_png is not None
    assert not texture_has_fully_transparent_pixels(sea_png)

    motion = glb.json.get("extras", {}).get("rae", {}).get("mapMaterialMotion") or {}
    sea_tracks = [
        track["material"]
        for clip in motion.get("clips", [])
        for track in clip.get("tracks", [])
        if "sea_iro" in track.get("material", "")
    ]
    assert not sea_tracks


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0005_stage_lights_export_with_alpha(tmp_path):
    from rae.platforms.threeds.gltf.apply import apply_glb_policy
    from rae.platforms.threeds.gltf.glb_io import material_texture_bytes, read_glb
    from rae.platforms.threeds.gltf.texture_alpha import texture_has_fully_transparent_pixels
    from rae.platforms.threeds.service import _load_world_model, load_textures, build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 5,
        "name": "Battle background 0005",
    }
    model = _load_world_model(descriptor)
    textures = load_textures(descriptor)
    tex_by_name = {tex.name: tex for tex in textures}
    stage = next(mat for mat in model.materials if mat.name == "btl_G_lili_stage")
    resolved = _resolve_material_texture_rgba(stage, tex_by_name)
    assert resolved is not None
    rgba, width, height = resolved
    assert all(rgba[i] == 255 for i in range(3, len(rgba), 4))

    out = tmp_path / "bb0005.glb"
    build_model_glb(descriptor, tmp_path)
    glb_path = tmp_path / "w_a_0_8_1_0005.glb"
    apply_glb_policy(glb_path)
    glb = read_glb(glb_path)
    materials = {m["name"]: m for m in glb.json["materials"]}
    stage_mat = materials["btl_G_lili_stage"]
    rae = stage_mat["extras"]["rae"]
    assert rae["renderClass"] == "opaque"
    png = material_texture_bytes(glb, glb.json["materials"].index(stage_mat))
    assert png is not None
    assert not texture_has_fully_transparent_pixels(png)

    fire_mat = materials["btl_G_lili_fire01"]
    assert fire_mat["extras"]["rae"]["renderClass"] == "additive"
    fire_png = material_texture_bytes(glb, glb.json["materials"].index(fire_mat))
    assert fire_png is not None
    assert texture_has_fully_transparent_pixels(fire_png)

    komo_mat = materials["btl_G_lili_komo01"]
    assert komo_mat["extras"]["rae"]["renderClass"] == "additive"
    komo_png = material_texture_bytes(glb, glb.json["materials"].index(komo_mat))
    assert komo_png is not None
    assert texture_has_fully_transparent_pixels(komo_png)

    shadow_mat = materials["btl_G_lili_shadow"]
    assert shadow_mat["extras"]["rae"]["renderClass"] in {"blend", "mask"}
    assert shadow_mat["extras"]["rae"]["renderClass"] != "additive"
