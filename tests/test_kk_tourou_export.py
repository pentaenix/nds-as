"""Regression tests for kk_tourou lamp export (dot keys, underscore PNG paths, partial alpha)."""
from __future__ import annotations

from pathlib import Path

import pytest

from rae.core.texture_sequences import (
    canonical_frame_key,
    detect_material_sequences,
    detect_texture_frame_families,
    ensure_default_play_state,
    list_playback_options,
    normalize_material_spec,
    resolve_frame_path,
)
from rae.glb_policy.glb_io import read_glb
from rae.glb_policy.texture_patch import write_patched_preview_glb

EXPORT_DIR = Path(__file__).resolve().parents[1] / "exports" / "dsm_model_a101f488dd048245_glb"


@pytest.mark.skipif(not EXPORT_DIR.is_dir(), reason="local export folder missing")
def test_underscore_png_stems_are_not_animation_families() -> None:
    keys = {
        "kk_tourou_b_1",
        "kk_tourou_b_2",
        "kk_tourou_b_3",
    }
    families = detect_texture_frame_families(keys)
    assert families == {}
    assert canonical_frame_key("kk_tourou_b_2") == "kk_tourou_b_2"


@pytest.mark.skipif(not EXPORT_DIR.is_dir(), reason="local export folder missing")
def test_underscore_pngs_discover_numbered_frames_for_animation_tab() -> None:
    keys = {"kk_tourou_b_1", "kk_tourou_b_2", "kk_tourou_b_3"}
    detected = detect_material_sequences(
        ["kk_tourou_b"],
        [EXPORT_DIR / "kk_tourou_b_1.png"],
        assignments={},
        available_texture_keys=keys,
    )
    assert detected["kk_tourou_b"]["frames"] == ["kk_tourou_b_1", "kk_tourou_b_2", "kk_tourou_b_3"]
    assert detected["kk_tourou_b"]["states"] == {}
    assert list_playback_options({"kk_tourou_b": detected["kk_tourou_b"]}) == []

    spec = ensure_default_play_state(normalize_material_spec(detected["kk_tourou_b"]))
    assert list_playback_options({"kk_tourou_b": spec})[0]["state"] == "play"

    path = resolve_frame_path(
        "kk_tourou_b_2",
        texture_by_name={},
        fallback_paths=list(EXPORT_DIR.glob("*.png")),
    )
    assert path is not None
    assert path.name == "kk_tourou_b_2.png"


@pytest.mark.skipif(not EXPORT_DIR.is_dir(), reason="local export folder missing")
def test_patch_frame_two_uses_blend_not_mask() -> None:
    glb_path = EXPORT_DIR / "kk_tourou.glb"
    out_path = EXPORT_DIR / ".rae_preview" / "test_frame2.glb"
    write_patched_preview_glb(
        glb_path,
        out_path,
        mesh_labels=["kk_tourou_b"],
        mesh_texture_paths=[EXPORT_DIR / "kk_tourou_b_2.png"],
    )
    patched = read_glb(out_path)
    material = next(m for m in patched.json["materials"] if m.get("name") == "kk_tourou_b")
    assert material["alphaMode"] == "BLEND"
    assert material["extras"]["rae"]["renderClass"] == "blend"
