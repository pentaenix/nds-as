from pathlib import Path

from rae.core.texture_sequences import (
    DEFAULT_PLAY_STATE,
    canonical_frame_key,
    detect_material_sequences,
    detect_texture_frame_families,
    ensure_default_play_state,
    family_for_texture_key,
    is_dot_animation_frame_key,
    is_numbered_frame_key,
    list_playback_options,
    load_texture_sequences,
    material_has_playable_states,
    normalize_material_spec,
    parse_frame_texture_key,
    playback_frames_for_spec,
    preview_frame_for_spec,
)


def test_parse_frame_texture_key_dot_and_underscore() -> None:
    assert parse_frame_texture_key("lamp.1") == ("lamp", 1, "dot")
    assert parse_frame_texture_key("wk_sp1_2") == ("wk_sp1", 2, "underscore")
    assert parse_frame_texture_key("plain") == ("plain", None, None)


def test_is_numbered_frame_key() -> None:
    assert is_numbered_frame_key("lamp.1")
    assert is_numbered_frame_key("lamp_1")
    assert not is_numbered_frame_key("plain")


def test_is_dot_animation_frame_key() -> None:
    assert is_dot_animation_frame_key("lamp.1")
    assert not is_dot_animation_frame_key("lamp_1")


def test_detect_texture_frame_families_dot_only() -> None:
    families = detect_texture_frame_families(
        {"lamp.1", "lamp.2", "lamp.3", "kk_tourou_b_1", "kk_tourou_b_2", "kk_tourou_b_3"}
    )
    assert "lamp" in families
    assert families["lamp"].frames == ("lamp.1", "lamp.2", "lamp.3")
    assert "kk_tourou_b" not in families


def test_detect_texture_frame_families_ignores_underscore_pairs() -> None:
    families = detect_texture_frame_families({"body_1", "body_2", "face_1", "face_2"})
    assert families == {}


def test_detect_material_sequences_maps_mesh_labels() -> None:
    keys = {"wk_sp1.1", "wk_sp1.2", "wk_sp1.3"}
    spec = detect_material_sequences(
        ["wk_sp1"],
        [Path("wk_sp1.1.png")],
        assignments={"wk_sp1": "wk_sp1.1"},
        available_texture_keys=keys,
    )
    assert "wk_sp1" in spec
    assert spec["wk_sp1"]["frames"] == ["wk_sp1.1", "wk_sp1.2", "wk_sp1.3"]
    assert spec["wk_sp1"]["states"] == {}


def test_detect_material_sequences_maps_underscore_pngs() -> None:
    keys = {"kk_tourou_b_1", "kk_tourou_b_2", "kk_tourou_b_3", "h_kage"}
    spec = detect_material_sequences(
        ["kk_tourou_b"],
        [Path("kk_tourou_b_1.png")],
        assignments={},
        available_texture_keys=keys,
    )
    assert "kk_tourou_b" in spec
    assert spec["kk_tourou_b"]["frames"] == ["kk_tourou_b_1", "kk_tourou_b_2", "kk_tourou_b_3"]
    assert "h_kage" not in spec


def test_detect_material_sequences_skips_single_texture_meshes() -> None:
    spec = detect_material_sequences(
        ["h_kage"],
        [Path("h_kage.png")],
        assignments={},
        available_texture_keys={"h_kage"},
    )
    assert spec == {}


def test_family_for_texture_key_dot_only() -> None:
    families = detect_texture_frame_families({"sign.1", "sign.2"})
    assert family_for_texture_key("sign.2", families) is not None
    underscore_families = detect_texture_frame_families({"sign_1", "sign_2"})
    assert underscore_families == {}
    assert family_for_texture_key("sign_2", underscore_families) is None


def test_family_for_texture_key_binds_underscore_material_to_dot_family() -> None:
    families = detect_texture_frame_families({"lamp.1", "lamp.2", "lamp.3"})
    assert family_for_texture_key("lamp_2", families) is not None


def test_canonical_frame_key_preserves_underscore() -> None:
    assert canonical_frame_key("lamp.2") == "lamp.2"
    assert canonical_frame_key("lamp_2") == "lamp_2"


def test_normalize_does_not_auto_create_states() -> None:
    spec = normalize_material_spec({"frames": ["lamp.1", "lamp.2", "lamp.3"], "frameStyle": "dot"})
    assert spec["states"] == {}
    assert list_playback_options({"mat": spec}) == []


def test_default_play_state() -> None:
    spec = ensure_default_play_state(
        {"frames": ["lamp.1", "lamp.2", "lamp.3"], "frameStyle": "dot"}
    )
    assert DEFAULT_PLAY_STATE in spec["states"]
    assert spec["states"][DEFAULT_PLAY_STATE]["frames"] == ["lamp.1", "lamp.2", "lamp.3"]
    assert list_playback_options({"mat": spec})[0]["state"] == DEFAULT_PLAY_STATE


def test_material_has_playable_states_includes_single_frame() -> None:
    spec = normalize_material_spec(
        {
            "frames": ["lamp.1", "lamp.2"],
            "frameStyle": "dot",
            "states": {"off": {"frames": ["lamp.1"], "animate": False}},
        }
    )
    assert material_has_playable_states(spec)


def test_list_playback_includes_static_states() -> None:
    spec = normalize_material_spec(
        {
            "frames": ["lamp.1", "lamp.2", "lamp.3"],
            "frameStyle": "dot",
            "states": {
                "off": {"frames": ["lamp.1"], "animate": False, "loop": False, "speed": 1.0},
                "on": {"frames": ["lamp.2", "lamp.3"], "animate": True, "loop": True, "speed": 1.0},
            },
        }
    )
    options = list_playback_options({"mat": spec})
    assert len(options) == 2
    by_state = {opt["state"]: opt for opt in options}
    assert by_state["off"]["animate"] is False
    assert by_state["on"]["animate"] is True


def test_list_playback_disambiguates_duplicate_state_names() -> None:
    body = ensure_default_play_state(
        {"frames": ["body.1", "body.2"], "frameStyle": "dot", "sequenceBase": "body"}
    )
    face = ensure_default_play_state(
        {"frames": ["face.1", "face.2"], "frameStyle": "dot", "sequenceBase": "face"}
    )
    options = list_playback_options({"body": body, "face": face})
    assert len(options) == 2
    labels = {opt["label"] for opt in options}
    assert labels == {"body · play", "face · play"}


def test_user_state_playback() -> None:
    spec = normalize_material_spec(
        {
            "frames": ["lamp.1", "lamp.2", "lamp.3", "lamp.4", "lamp.5", "lamp.6"],
            "frameStyle": "dot",
            "states": {
                "a": {"frames": ["lamp.1", "lamp.2", "lamp.3"], "animate": True, "loop": True, "speed": 1.0},
                "b": {"frames": ["lamp.4", "lamp.5"], "animate": True, "loop": True, "speed": 1.0},
            },
            "activeState": "a",
        }
    )
    frames, animate, loop, duration = playback_frames_for_spec(spec, state_name="b")
    assert frames == ["lamp.4", "lamp.5"]
    assert animate is True
    assert loop is True
    assert preview_frame_for_spec(spec, state_name="a") == "lamp.1"
    options = list_playback_options({"mat": spec})
    assert len(options) == 2
    assert options[0]["label"] == "a"
    assert options[1]["label"] == "b"


def test_load_texture_sequences_from_manifest() -> None:
    manifest = {
        "texture_sequences": {
            "asset1": {
                "materials": {
                    "lamp_mat": {
                        "sequenceBase": "lamp",
                        "frameStyle": "dot",
                        "frames": ["lamp.1", "lamp.2"],
                        "frameDurationMs": 200,
                        "loop": True,
                        "states": {
                            "blink": {"frames": ["lamp.1", "lamp.2"], "animate": True, "loop": True, "speed": 1.0},
                        },
                        "activeState": "blink",
                    }
                }
            }
        }
    }
    loaded = load_texture_sequences(manifest)
    assert loaded["asset1"]["materials"]["lamp_mat"]["frameDurationMs"] == 200
    assert "blink" in loaded["asset1"]["materials"]["lamp_mat"]["states"]


def test_load_texture_sequences_keeps_underscore_frames() -> None:
    manifest = {
        "texture_sequences": {
            "asset1": {
                "materials": {
                    "body": {
                        "frameStyle": "dot",
                        "frames": ["body_1", "body_2"],
                        "states": {
                            "on": {"frames": ["body_1", "body_2"], "animate": True, "loop": True, "speed": 1.0},
                        },
                        "activeState": "on",
                    }
                }
            }
        }
    }
    loaded = load_texture_sequences(manifest)
    assert loaded["asset1"]["materials"]["body"]["frames"] == ["body_1", "body_2"]
