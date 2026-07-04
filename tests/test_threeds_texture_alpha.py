"""3DS texture decode and GLB transparency export."""
from __future__ import annotations

import struct

from rae.glb_policy.glb_io import material_texture_bytes, read_glb
from rae.glb_policy.texture_alpha import texture_has_partial_alpha_channel
from rae.platforms.threeds.pica import PICA_FORMATS, decode_pica_texture, rgba_to_png


def test_la8_decodes_luminance_then_alpha():
    # One 8x8 tile, two LA8 texels: (L=200, A=64) and (L=40, A=220).
    data = bytearray(64 * 2)
    data[0] = 200
    data[1] = 64
    data[2] = 40
    data[3] = 220
    rgba = decode_pica_texture(bytes(data), 8, 8, PICA_FORMATS["LA8"])
    assert rgba[0:4] == bytes((200, 200, 200, 64))
    assert rgba[4:8] == bytes((40, 40, 40, 220))


def test_partial_alpha_png_classified(tmp_path):
    from rae.platforms.threeds.glb import write_model_glb
    from rae.platforms.threeds.gf import GfMaterial, GfMesh, GfModel, GfSubMesh, GfTexture
    la = bytearray(64 * 2)
    for i in range(64):
        la[i * 2] = 180
        la[i * 2 + 1] = 0 if i < 16 else 96
    tex = GfTexture(
        name="water.tga",
        width=8,
        height=8,
        gf_format=0x23,
        pica_format=PICA_FORMATS["LA8"],
        raw=bytes(la),
    )
    sub = GfSubMesh(
        material_name="water_mat",
        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        normals=[(0.0, 1.0, 0.0)] * 3,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        indices=[0, 1, 2],
    )
    mat = GfMaterial(
        name="water_mat",
        texture_names=["water.tga"],
        blend=(255, 255, 255, 96),
    )
    model = GfModel(
        name="water",
        materials=[mat],
        meshes=[GfMesh(name="plane", submeshes=[sub])],
    )
    out = tmp_path / "water.glb"
    write_model_glb(model, [tex], out)
    glb = read_glb(out)
    material = glb.json["materials"][0]
    assert material.get("alphaMode") == "BLEND"
    assert material["extras"]["rae"]["renderClass"] == "blend"
    png = material_texture_bytes(glb, 0)
    assert png is not None
    assert texture_has_partial_alpha_channel(png)


def test_zero_blend_alpha_does_not_force_mask(tmp_path):
    """GF uses alpha=0 in blend/diffuse to mean unused, not fully transparent."""
    from rae.platforms.threeds.glb import _bake_gf_texture_colors, write_model_glb
    from rae.platforms.threeds.gf import GfMaterial, GfMesh, GfModel, GfSubMesh, GfTexture

    la = bytearray(64 * 2)
    for i in range(64):
        la[i * 2] = 70 if i % 2 == 0 else 20
        la[i * 2 + 1] = 255
    tex = GfTexture(
        name="waves.tga",
        width=8,
        height=8,
        gf_format=0x23,
        pica_format=PICA_FORMATS["LA8"],
        raw=bytes(la),
    )
    sub = GfSubMesh(
        material_name="waves_mat",
        positions=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)],
        normals=[(0.0, 1.0, 0.0)] * 3,
        uvs=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        indices=[0, 1, 2],
    )
    mat = GfMaterial(
        name="waves_mat",
        texture_names=["waves.tga"],
        blend=(255, 255, 255, 0),
        diffuse=(0, 0, 0, 0),
        emission=(255, 255, 255, 255),
    )
    baked = _bake_gf_texture_colors(tex.decode_rgba(), mat)
    assert baked[0] == 70

    model = GfModel(
        name="waves",
        materials=[mat],
        meshes=[GfMesh(name="plane", submeshes=[sub])],
    )
    out = tmp_path / "waves.glb"
    write_model_glb(model, [tex], out)
    glb = read_glb(out)
    material = glb.json["materials"][0]
    assert material.get("alphaMode") == "BLEND"
    assert material["extras"]["rae"]["renderClass"] == "additive"
    assert material["extras"]["rae"]["nitro"]["alpha"] == 1.0
    assert material["extras"]["rae"]["nitro"]["blendMode"] == "additive"


def test_pokemon_emission_not_baked():
    from rae.platforms.threeds.glb import _bake_gf_texture_colors
    from rae.platforms.threeds.gf import GfMaterial

    rgba = bytes((0, 0, 0, 255))
    mat = GfMaterial(
        name="BodyA",
        texture_names=["body.tga"],
        emission=(94, 94, 94, 0),
    )
    baked = _bake_gf_texture_colors(rgba, mat)
    assert baked == rgba


def test_diffuse_tint_baked_into_texture():
    from rae.platforms.threeds.glb import _bake_gf_texture_colors
    from rae.platforms.threeds.gf import GfMaterial

    rgba = bytes((128, 128, 128, 255)) * (8 * 8)
    mat = GfMaterial(
        name="sea_mat",
        texture_names=["sea.tga"],
        diffuse=(69, 92, 147, 0),
    )
    baked = _bake_gf_texture_colors(rgba, mat)
    assert baked[0:3] == bytes((34, 46, 73))

    colored = bytes((100, 150, 200, 255)) * (8 * 8)
    baked_colored = _bake_gf_texture_colors(colored, mat)
    assert baked_colored[0:3] == bytes((100, 150, 200))
