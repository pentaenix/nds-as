"""Battle / world map GFMotion export and GLB mapMaterialMotion extras."""
from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.threeds

from rae.platforms.threeds.motion import (
    bake_world_map_motion_clip,
    build_world_map_material_motion,
    dedupe_world_motions,
    parse_gf_motion,
    pick_default_world_motion_clip,
    world_motion_effective_loop,
)
from rae.platforms.threeds.service import load_world_motions


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_load_world_motions_dedupes_slot_8() -> None:
    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 8,
    }
    motions = load_world_motions(descriptor)
    assert len(motions) >= 10
    unique = dedupe_world_motions(motions)
    assert len(unique) < len(motions)
    ambient = next(m for m in unique if m.frames_count == 375 and m.is_looping)
    assert world_motion_effective_loop(ambient)
    assert any(t.name == "btl_G_hama_Unami05" for t in ambient.material_tracks)


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0008_exports_map_material_motion(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import read_glb
    from rae.platforms.threeds.service import build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 8,
        "name": "Battle background 0008",
    }
    out = build_model_glb(descriptor, tmp_path)
    glb = read_glb(out)
    motion = glb.json.get("extras", {}).get("rae", {}).get("mapMaterialMotion")
    assert motion is not None
    assert motion["frameRate"] == 30
    assert motion["defaultClip"]
    clips = motion["clips"]
    assert clips
    default = next(c for c in clips if c["id"] == motion["defaultClip"])
    materials = {track["material"] for track in default["tracks"]}
    assert "btl_G_hama_Unami05" in materials
    assert "btl_G_kusa_kusa01" in materials
    assert "btl_G_hama_jime01" not in materials
    grass = next(t for t in default["tracks"] if t["material"] == "btl_G_kusa_kusa01")
    assert grass.get("motionKind") == "wind"
    offsets = grass["frameOffsets"]
    max_dx = max(abs(ox) for ox, _oy in offsets)
    assert max_dx < 0.05, "grass wind should be a subtle oscillation, not a scroll"
    assert "btl_A_sea_iro045" not in materials
    assert motion["defaultClip"] != "ambient_composite"
    assert "overlayClips" not in motion or not motion.get("overlayClips")
    unami = next(t for t in default["tracks"] if t["material"] == "btl_G_hama_Unami05")
    assert len(unami["frameOffsets"]) == 376
    assert unami["frameOffsets"][0] != unami["frameOffsets"][100]
    assert glb.json.get("animations"), "visibility clips should export as glTF animations"


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0005_fire_motion_export(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import read_glb
    from rae.platforms.threeds.service import _load_world_model, build_model_glb, load_world_motions

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 5,
        "name": "Battle background 0005",
    }
    model = _load_world_model(descriptor)
    motions = dedupe_world_motions(load_world_motions(descriptor))
    fire_clip = next(
        m
        for m in motions
        if m.frames_count == 375 and any(t.name == "btl_G_lili_fire01" for t in m.material_tracks)
    )
    assert world_motion_effective_loop(fire_clip)
    baked = bake_world_map_motion_clip(
        fire_clip,
        model,
        material_names={mat.name for mat in model.materials},
    )
    fire = next(t for t in baked if t["material"] == "btl_G_lili_fire01")
    assert len(fire["frameOffsets"]) == 376
    assert abs(fire["frameOffsets"][-1][1]) > 1.0

    out = build_model_glb(descriptor, tmp_path)
    glb = read_glb(out)
    motion = glb.json["extras"]["rae"]["mapMaterialMotion"]
    assert motion["defaultClip"]
    assert "mapMaterialMotion" not in (glb.json.get("materials") or [{}])[0].get("extras", {}).get("rae", {})


def test_pick_default_world_motion_clip_prefers_long_ambient() -> None:
    clips = [
        {"id": "weather_80f_once_01", "frameCount": 81, "loop": False, "tracks": [{"material": "rain"}]},
        {
            "id": "ambient_19f_loop_02",
            "frameCount": 20,
            "loop": True,
            "tracks": [{"material": "jime"}],
        },
        {
            "id": "ambient_480f_loop_10",
            "frameCount": 481,
            "loop": True,
            "tracks": [{"material": "btl_A_sea_iro045"}],
        },
        {
            "id": "ambient_375f_loop_00",
            "frameCount": 376,
            "loop": True,
            "tracks": [{"material": "btl_G_hama_Unami05"}],
        },
    ]
    assert pick_default_world_motion_clip(clips) == "ambient_375f_loop_00"


def test_pick_ambient_overlay_clips_layers_sea_caustics() -> None:
    from rae.platforms.threeds.motion import pick_ambient_overlay_clips

    clips = [
        {
            "id": "ambient_375f_loop_00",
            "frameCount": 376,
            "loop": True,
            "tracks": [{"material": "btl_G_hama_Unami05"}],
        },
        {
            "id": "ambient_480f_loop_10",
            "frameCount": 481,
            "loop": True,
            "tracks": [{"material": "btl_A_sea_iro045"}],
        },
    ]
    assert pick_ambient_overlay_clips(clips, "ambient_375f_loop_00") == []


def test_kusa_grass_wind_bakes_unit0_only() -> None:
    from rae.platforms.threeds.gf import GfMaterial, GfModel, GfTextureUnit
    from rae.platforms.threeds.motion import GfMotUVTrack, GfMotion, GfMotKey

    motion = GfMotion(name="wind", frames_count=375, is_looping=True)
    motion.material_tracks.append(
        GfMotUVTrack(
            name="btl_G_kusa_kusa01",
            unit_index=0,
            channels=[
                [],
                [],
                [],
                [GfMotKey(0.0, -0.02, 0.0), GfMotKey(187.0, -0.001, 0.0), GfMotKey(375.0, -0.02, 0.0)],
                [],
            ],
        )
    )
    motion.material_tracks.append(
        GfMotUVTrack(
            name="btl_G_kusa_kusa01",
            unit_index=1,
            channels=[
                [],
                [],
                [],
                [GfMotKey(0.0, -0.02, 0.0), GfMotKey(187.0, -0.01, 0.0), GfMotKey(375.0, -0.02, 0.0)],
                [],
            ],
        )
    )
    mat = GfMaterial(
        name="btl_G_kusa_kusa01",
        texture_units=[
            GfTextureUnit(
                name="grass.tga",
                unit_index=0,
                scale=(1.0, 1.0),
                rotation=0.0,
                translation=(-0.02, 0.0),
            ),
            GfTextureUnit(
                name="grass.tga",
                unit_index=1,
                scale=(-3.0, 1.2),
                rotation=0.0,
                translation=(-0.02, 0.0),
            ),
        ],
    )
    model = GfModel(name="test", materials=[mat], meshes=[], bones=[])
    baked = bake_world_map_motion_clip(motion, model, material_names={"btl_G_kusa_kusa01"})
    assert len(baked) == 1
    assert baked[0]["material"] == "btl_G_kusa_kusa01"
    assert baked[0]["motionKind"] == "wind"


def test_kusa_jime_still_skipped_from_uv_bake() -> None:
    from rae.platforms.threeds.gf import GfMaterial, GfModel, GfTextureUnit
    from rae.platforms.threeds.motion import GfMotUVTrack, GfMotion, GfMotKey

    motion = GfMotion(name="wet", frames_count=19, is_looping=True)
    motion.material_tracks.append(
        GfMotUVTrack(
            name="btl_G_kusa_jime01",
            unit_index=2,
            channels=[
                [],
                [],
                [],
                [GfMotKey(0.0, 0.0, 0.0), GfMotKey(19.0, 1.0, 0.0)],
                [GfMotKey(0.0, 0.0, 0.0), GfMotKey(19.0, 0.5, 0.0)],
            ],
        )
    )
    mat = GfMaterial(
        name="btl_G_kusa_jime01",
        texture_units=[
            GfTextureUnit(
                name="sand.tga",
                unit_index=0,
                scale=(1.0, 1.0),
                rotation=0.0,
                translation=(0.0, 0.0),
            ),
        ],
    )
    model = GfModel(name="test", materials=[mat], meshes=[], bones=[])
    baked = bake_world_map_motion_clip(motion, model, material_names={"btl_G_kusa_jime01"})
    assert baked == []


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0004_skips_overlay_flower_scroll(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import read_glb
    from rae.platforms.threeds.service import build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 4,
        "name": "Battle background 0004",
    }
    out = build_model_glb(descriptor, tmp_path)
    glb = read_glb(out)
    motion = glb.json["extras"]["rae"]["mapMaterialMotion"]
    default = next(c for c in motion["clips"] if c["id"] == motion["defaultClip"])
    materials = {t["material"]: t for t in default["tracks"]}
    assert "btl_G_f_ye_ueki04" not in materials
    ueki02 = materials["btl_G_f_ye_ueki02"]
    assert ueki02["motionKind"] == "wind"
    assert max(abs(x) for x, _ in ueki02["frameOffsets"]) < 0.12


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0018_grass_wind_on_siba_kusa(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import read_glb
    from rae.platforms.threeds.service import build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 18,
        "name": "Battle background 0018",
    }
    out = build_model_glb(descriptor, tmp_path)
    glb = read_glb(out)
    motion = glb.json["extras"]["rae"]["mapMaterialMotion"]
    default = next(c for c in motion["clips"] if c["id"] == motion["defaultClip"])
    materials = {t["material"] for t in default["tracks"]}
    assert "btl_G_siba_kusa01" in materials
    grass = next(t for t in default["tracks"] if t["material"] == "btl_G_siba_kusa01")
    assert grass["motionKind"] == "wind"


@pytest.mark.skipif(
    not Path(__file__).resolve().parent.parent.joinpath("roms/Pokemon Ultra Moon.cci").is_file(),
    reason="Ultra Moon test ROM not present",
)
def test_battle_background_0007_grass_only_default_clip(tmp_path: Path) -> None:
    from rae.platforms.threeds.gltf.glb_io import read_glb
    from rae.platforms.threeds.service import build_model_glb

    descriptor = {
        "type": "world_model",
        "rom": str(Path(__file__).resolve().parent.parent / "roms" / "Pokemon Ultra Moon.cci"),
        "garc": "/a/0/8/1",
        "slot": 7,
        "name": "Battle background 0007",
    }
    out = build_model_glb(descriptor, tmp_path)
    glb = read_glb(out)
    motion = glb.json["extras"]["rae"]["mapMaterialMotion"]
    default = next(c for c in motion["clips"] if c["id"] == motion["defaultClip"])
    materials = {t["material"] for t in default["tracks"]}
    assert "btl_G_kusa_kusa01" in materials
    assert default["tracks"], "grass-only maps must still export a playable ambient clip"


def test_jime_material_skipped_from_uv_bake() -> None:
    from rae.platforms.threeds.gf import GfMaterial, GfModel, GfTextureUnit
    from rae.platforms.threeds.motion import GfMotUVTrack, GfMotion, GfMotKey

    motion = GfMotion(name="wet", frames_count=19, is_looping=True)
    motion.material_tracks.append(
        GfMotUVTrack(
            name="btl_G_hama_jime01",
            unit_index=2,
            channels=[
                [],
                [],
                [],
                [GfMotKey(0.0, 0.0, 0.0), GfMotKey(19.0, 1.0, 0.0)],
                [GfMotKey(0.0, 0.0, 0.0), GfMotKey(19.0, 0.5, 0.0)],
            ],
        )
    )
    mat = GfMaterial(
        name="btl_G_hama_jime01",
        texture_units=[
            GfTextureUnit(
                name="sand.tga",
                unit_index=0,
                scale=(1.0, 1.0),
                rotation=0.0,
                translation=(0.0, 0.0),
            ),
        ],
    )
    model = GfModel(name="test", materials=[mat], meshes=[], bones=[])
    baked = bake_world_map_motion_clip(motion, model, material_names={"btl_G_hama_jime01"})
    assert baked == []


def test_bake_material_motion_still_eye_only() -> None:
    from rae.platforms.threeds.motion import bake_material_motion
    from tests.test_threeds_material_motion import _motion_with_material

    motion = parse_gf_motion(_motion_with_material(5), "blink")
    rest = {
        "btl_G_hama_Unami05": (1.0, 1.0, 0.0, 0.0),
        "Eye": (2.0, 1.0, 1.0, 0.0),
    }
    baked = bake_material_motion(motion, rest)
    assert {b.material for b in baked} == {"Eye"}


def test_build_world_map_material_motion_from_synthetic() -> None:
    from tests.test_threeds_material_motion import _motion_with_material

    from rae.platforms.threeds.gf import GfMaterial, GfModel, GfTextureUnit

    motion = parse_gf_motion(_motion_with_material(4), "scroll")
    motion.material_tracks[0].name = "btl_G_lili_fire01"
    mat = GfMaterial(
        name="btl_G_lili_fire01",
        texture_units=[
            GfTextureUnit(
                name="btl_G_lili_fire01.tga",
                unit_index=0,
                scale=(1.0, 1.0),
                rotation=0.0,
                translation=(0.0, 0.0),
                wrap_u=2,
                wrap_v=2,
            ),
        ],
    )
    model = GfModel(name="test", materials=[mat], meshes=[], bones=[])
    extras = build_world_map_material_motion([motion], model, material_names={"btl_G_lili_fire01"})
    assert extras is not None
    assert extras["clips"][0]["tracks"][0]["material"] == "btl_G_lili_fire01"
