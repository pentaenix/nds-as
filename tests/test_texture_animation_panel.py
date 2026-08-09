from pathlib import Path

import pytest

from rae.ui.main.texture_animation_panel import TextureAnimationPanelMixin


pytestmark = pytest.mark.nds


class _Preview:
    _fallback_texture_paths = [Path("/tmp/water.1.png"), Path("/tmp/water.2.png")]
    _texture_by_name = {
        "water.1": Path("/tmp/water.1.png"),
        "water.2": Path("/tmp/water.2.png"),
    }
    _mesh_texture_paths = [Path("/tmp/water.1.png")]
    _material_to_texture = {"water_mat": "water.1"}
    _last_path = None


class _Panel(TextureAnimationPanelMixin):
    def __init__(self) -> None:
        self.preview = _Preview()
        self._texture_assignments = {}
        self._preview_mesh_labels = ["water_mat"]
        self._texture_sequences = {}


def test_detected_frame_family_gets_default_play_state() -> None:
    panel = _Panel()

    panel._sync_texture_sequences("water-model")

    spec = panel._texture_sequences["water-model"]["materials"]["water_mat"]
    assert spec["activeState"] == "play"
    assert spec["states"]["play"] == {
        "frames": ["water.1", "water.2"],
        "animate": True,
        "loop": True,
        "speed": 1.0,
    }
