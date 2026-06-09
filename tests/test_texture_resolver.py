from pathlib import Path

import pytest

from rae.glb_preview_textures import build_mesh_texture_paths
from rae.model_texture_resolver import build_preview_texture_maps, resolve_model_textures
from rae.nitro_models import MaterialBinding
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


def test_build_preview_texture_maps_uses_manifest_texture_order():
    from rae.model_texture_resolver import ModelTextureResolution
    from rae.nitro_models import NsbmdManifest
    from rae.nitro_textures import DecodedImage

    manifest = NsbmdManifest(
        materials=[
            MaterialBinding("wall_mat", "wall_tex", "wall_pl"),
            MaterialBinding("trim_mat", "trim_tex", "trim_pl"),
            MaterialBinding("sign_mat", "wall_tex", "wall_pl"),
        ],
    )
    rgba = b"\x00" * (8 * 8 * 4)
    images = [
        DecodedImage("wall_tex__wall_pl", 8, 8, rgba, "embedded"),
        DecodedImage("wall_tex__alt_pl", 8, 8, rgba, "embedded"),
        DecodedImage("trim_tex__trim_pl", 8, 8, rgba, "embedded"),
    ]
    paths = [Path(f"/tmp/{img.name}.png") for img in images]
    resolution = ModelTextureResolution(
        "embedded_texture",
        manifest,
        [],
        images,
        [],
        [],
        "",
    )
    texture_by_name, material_to_texture, bind_order = build_preview_texture_maps(resolution, paths)
    assert bind_order == ["wall_tex", "trim_tex"]
    assert material_to_texture["wall_mat"] == "wall_tex"
    assert texture_by_name["wall_mat"] == paths[0]


def test_discover_colocated_textures_indexes_png_stems(tmp_path):
    from rae.glb_preview_textures import discover_colocated_textures

    glb = tmp_path / "pc_center.glb"
    glb.write_bytes(b"glb")
    wall = tmp_path / "kabe.tga.png"
    wall.write_bytes(b"png")
    trim = tmp_path / "kabe_pl.tga.png"
    trim.write_bytes(b"png")

    found = discover_colocated_textures(glb)
    assert found["kabe.tga"] == wall
    assert found["kabe_pl.tga"] == trim


def test_build_mesh_texture_paths_uses_colocated_apicula_png(tmp_path):
    from types import SimpleNamespace

    from rae.glb_preview_textures import build_mesh_texture_paths

    glb = tmp_path / "model.glb"
    glb.write_bytes(b"glb")
    tex = tmp_path / "wall_mat.png"
    tex.write_bytes(b"png")

    mesh = SimpleNamespace(
        visual=SimpleNamespace(
            kind="texture",
            material=SimpleNamespace(name="wall_mat", image=None),
            uv=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        ),
        metadata={},
    )
    paths = build_mesh_texture_paths(
        [("wall_mat", mesh)],
        glb_path=glb,
        texture_by_name={},
        material_to_texture={},
        texture_bind_order=[],
        fallback_paths=[],
    )
    assert paths == [tex]


def test_build_mesh_texture_paths_uses_glb_material_table_not_mesh_index(tmp_path):
    import json
    import struct
    from types import SimpleNamespace

    from rae.glb_preview_textures import build_mesh_texture_paths

    wall = tmp_path / "wall_tex.png"
    trim = tmp_path / "trim_tex.png"
    wall.write_bytes(b"png")
    trim.write_bytes(b"png")

    gltf = {
        "asset": {"version": "2.0"},
        "images": [{"uri": "wall_tex.png"}, {"uri": "trim_tex.png"}],
        "textures": [{"source": 0, "sampler": 0}, {"source": 1, "sampler": 0}],
        "samplers": [{}],
        "materials": [
            {"name": "wall_mat", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            {"name": "trim_mat", "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}},
        ],
    }
    json_bytes = json.dumps(gltf).encode("utf-8")
    pad = (4 - (len(json_bytes) % 4)) % 4
    json_bytes += b" " * pad
    glb = tmp_path / "model.glb"
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes))
    chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    glb.write_bytes(header + chunk)

    def make_mesh(name: str):
        return SimpleNamespace(
            visual=SimpleNamespace(
                kind="texture",
                material=SimpleNamespace(name=name, image=None),
                uv=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
            ),
            metadata={},
        )

    named = [("trim_mat", make_mesh("trim_mat")), ("wall_mat", make_mesh("wall_mat"))]
    mesh_paths = build_mesh_texture_paths(
        named,
        glb_path=glb,
        texture_by_name={},
        material_to_texture={},
        # Reversed bind order would swap textures if we used mesh_index round-robin.
        texture_bind_order=["trim_tex", "wall_tex"],
        fallback_paths=[trim, wall],
    )
    assert mesh_paths == [trim, wall]


def test_build_mesh_texture_paths_uses_manifest_order_for_unknown_material_names():
    from types import SimpleNamespace

    class FakeMaterial:
        def __init__(self, name: str):
            self.name = name
            self.image = None

    class FakeVisual:
        def __init__(self, name: str):
            self.kind = "texture"
            self.material = FakeMaterial(name)
            self.uv = [(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)]

    class FakeMesh:
        def __init__(self, name: str):
            self.visual = FakeVisual(name)
            self.metadata = {}
            self.vertices = [0, 0, 0]
            self.faces = [[0, 1, 2]]

    paths = [Path("/tmp/wall.png"), Path("/tmp/trim.png")]
    named = [("mesh_0", FakeMesh("")), ("mesh_1", FakeMesh(""))]
    mesh_paths = build_mesh_texture_paths(
        named,
        texture_by_name={"wall_tex": paths[0], "trim_tex": paths[1]},
        material_to_texture={},
        texture_bind_order=["wall_tex", "trim_tex"],
        fallback_paths=paths,
    )
    assert mesh_paths == paths


def test_resolve_assignment_paths_maps_saved_keys_to_pngs(tmp_path):
    from PIL import Image

    from rae.core.texture_assignments import resolve_assignment_paths

    low = tmp_path / "wall_tex__preview.png"
    high = tmp_path / "wall_tex.png"
    trim = tmp_path / "trim_tex.png"
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(low)
    Image.new("RGBA", (128, 128), (0, 255, 0, 255)).save(high)
    Image.new("RGBA", (64, 64), (0, 0, 255, 255)).save(trim)
    paths = resolve_assignment_paths(
        {"wall_mat": "wall_tex", "trim_mat": "trim_tex"},
        texture_by_name={"wall_tex": low, "trim_tex": trim},
        fallback_paths=[low, trim],
    )
    assert paths["wall_mat"] == high
    assert paths["trim_mat"] == trim


def test_build_best_path_index_prefers_highest_resolution(tmp_path):
    from PIL import Image

    from rae.core.texture_assignments import build_best_path_index, estimate_assignments_from_paths

    low = tmp_path / "boat_tex__hash.png"
    high = tmp_path / "boat_tex.png"
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(low)
    Image.new("RGBA", (64, 32), (0, 255, 0, 255)).save(high)
    index = build_best_path_index([low, high])
    assert index["boat_tex"] == high
    assert index["boat_tex__hash"] == low

    estimated = estimate_assignments_from_paths(
        ["hull_mat", "sail_mat"],
        [high, low],
    )
    assert estimated == {"hull_mat": "boat_tex", "sail_mat": "boat_tex__hash"}


def test_parse_glb_material_preview_states_reads_alpha(tmp_path):
    import json
    import struct

    from rae.glb_preview_textures import (
        MaterialPreviewState,
        apply_material_preview_alpha,
        parse_glb_material_preview_states,
    )

    gltf = {
        "materials": [
            {
                "name": "glass_mat",
                "alphaMode": "BLEND",
                "pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 0.4]},
            },
            {
                "name": "fence_mat",
                "alphaMode": "MASK",
                "alphaCutoff": 0.6,
                "pbrMetallicRoughness": {"baseColorFactor": [1.0, 1.0, 1.0, 1.0]},
            },
        ]
    }
    json_bytes = json.dumps(gltf).encode("utf-8")
    glb = tmp_path / "model.glb"
    header = struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(json_bytes))
    chunk = struct.pack("<II", len(json_bytes), 0x4E4F534A) + json_bytes
    glb.write_bytes(header + chunk)

    states = parse_glb_material_preview_states(glb)
    assert states["glass_mat"] == MaterialPreviewState(
        alpha=0.4, alpha_mode="BLEND", alpha_cutoff=0.5, double_sided=False
    )
    assert states["fence_mat"].alpha_mode == "MASK"
    assert states["fence_mat"].alpha_cutoff == 0.6

    import numpy as np

    colors = np.array([[1.0, 0.0, 0.0, 0.8], [0.0, 1.0, 0.0, 0.4]], dtype=float)
    masked = apply_material_preview_alpha(colors, states["fence_mat"])
    assert masked[0, 3] == 0.0
    assert masked[1, 3] == 0.0
    blended = apply_material_preview_alpha(colors, states["glass_mat"])
    assert blended[0, 3] == pytest.approx(0.32)
    assert blended[1, 3] == pytest.approx(0.16)


def test_mesh_texture_override_matches_material_name(tmp_path):
    from types import SimpleNamespace

    from rae.glb_preview_textures import build_mesh_texture_paths

    tex = tmp_path / "gate_2.png"
    tex.write_bytes(b"png")
    mesh = SimpleNamespace(
        visual=SimpleNamespace(
            kind="texture",
            material=SimpleNamespace(name="gate_2_lm1", image=None),
            uv=[(0.0, 0.0), (1.0, 0.0), (0.0, 1.0)],
        ),
        metadata={},
    )
    paths = build_mesh_texture_paths(
        [("mesh_3", mesh)],
        mesh_texture_overrides={"gate_2_lm1": tex},
    )
    assert paths == [tex]


def test_preview_blend_mode_treats_mask_as_cutout_not_blend():
    import numpy as np

    from rae.glb_preview_textures import MaterialPreviewState
    from rae.ui.preview.glb_preview import GlbPreviewMixin

    class Preview(GlbPreviewMixin):
        pass

    preview = Preview()
    cutout = np.zeros((4, 4, 4), dtype=np.uint8)
    cutout[:, :, 3] = 255
    cutout[0, :, 3] = 0
    state = MaterialPreviewState(alpha=1.0, alpha_mode="MASK", alpha_cutoff=0.5)
    assert preview._preview_blend_mode(state, cutout, np) == "cutout"

    blended = cutout.copy()
    blended[1, 1, 3] = 128
    assert preview._preview_blend_mode(state, blended, np) == "cutout"

    blend_state = MaterialPreviewState(alpha=0.4, alpha_mode="BLEND", alpha_cutoff=0.5)
    assert preview._preview_blend_mode(blend_state, blended, np) == "blend"

    binary_blend = MaterialPreviewState(alpha=1.0, alpha_mode="BLEND", alpha_cutoff=0.5)
    assert preview._preview_blend_mode(binary_blend, cutout, np) == "cutout"

    fade_state = MaterialPreviewState(alpha=0.29, alpha_mode="BLEND", alpha_cutoff=0.5)
    opaque = np.full((4, 4, 4), 255, dtype=np.uint8)
    assert preview._preview_blend_mode(fade_state, opaque, np) == "fade"


def test_texture_baked_geometry_emits_solid_texel_quads():
    from types import SimpleNamespace

    import numpy as np
    from PIL import Image

    from rae.ui.preview.glb_preview import GlbPreviewMixin

    class Preview(GlbPreviewMixin):
        pass

    img = Image.new("RGBA", (4, 4), (0, 0, 0, 255))
    for x in range(4):
        for y in range(4):
            color = (255, 0, 0, 255) if (x + y) % 2 else (0, 255, 0, 255)
            img.putpixel((x, y), color)

    mesh = SimpleNamespace(
        faces=np.array([[0, 1, 2]], dtype=int),
        vertices=np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0]], dtype=float),
        visual=SimpleNamespace(
            kind="texture",
            material=SimpleNamespace(image=img, name="mat"),
            uv=np.array([[0.0, 1.0], [1.0, 1.0], [0.0, 0.0]], dtype=float),
        ),
    )
    preview = Preview()
    preview._mesh_texture_paths = []
    preview._fallback_texture_images = {}
    preview._fallback_texture_path_order = []
    preview._texture_by_name = {}

    baked = preview._texture_baked_display_geometry(
        mesh,
        np.array([[0, 0, 0], [4, 0, 0], [0, 4, 0]], dtype=float),
        mesh.faces,
        np,
        mesh_index=0,
        geometry_name="mat",
    )
    assert baked is not None
    verts, faces, colors = baked
    assert len(verts) > 3
    assert len(colors) == len(verts)
    # Each emitted triangle should be a solid texel (no Gouraud gradient).
    for tri in faces:
        tri_colors = colors[tri]
        assert np.allclose(tri_colors[0], tri_colors[1])
        assert np.allclose(tri_colors[1], tri_colors[2])


def test_best_path_for_key_prefers_colocated_full_size(tmp_path):
    from PIL import Image

    from rae.glb_preview_textures import _best_path_for_key

    low = tmp_path / "trim_tex__resolver.png"
    high = tmp_path / "trim_tex.png"
    Image.new("RGBA", (16, 16), (255, 0, 0, 255)).save(low)
    Image.new("RGBA", (256, 256), (0, 255, 0, 255)).save(high)
    picked = _best_path_for_key(
        "trim_tex",
        colocated={"trim_tex": high},
        texture_by_name={"trim_tex__resolver": low, "trim_tex": low},
        glb_path=tmp_path / "model.glb",
    )
    assert picked == high


def test_deferred_resolver_skips_library_for_embedded_model():
    btx = make_btx0_4bpp()
    tex0 = btx[0x14:]
    model_data = b"BMD0" + b"\0" * 32 + tex0
    model = asset("model", "models/embedded.nsbmd", "BMD0", model_data)
    resolution = resolve_model_textures(model, [model], defer_library_build=True)
    assert resolution.status == "embedded_texture"
    assert resolution.decoded_images
