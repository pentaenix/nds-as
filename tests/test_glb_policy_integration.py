from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from rae.glb_policy.apply import apply_glb_policy
from rae.glb_policy.classify import RenderClass
from rae.glb_policy.glb_io import read_glb_json

EN_PC = Path(__file__).resolve().parents[1] / "exports" / "dsm_model_07420f11bbe02f78_glb" / "en_pc.glb"


@pytest.mark.skipif(not EN_PC.is_file(), reason="local en_pc export missing")
def test_en_pc_shadow_material_classified_without_names() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        dest_dir = Path(tmp) / "glb"
        shutil.copytree(EN_PC.parent, dest_dir)
        glb_path = dest_dir / EN_PC.name
        results = apply_glb_policy(glb_path)
        gltf = read_glb_json(glb_path)
        materials = gltf["materials"]
        by_name = {m.get("name"): m for m in materials}
        shadow = by_name["h_kage"]
        assert results[3].render_class == RenderClass.UNIFORM_DECAL
        assert shadow["extras"]["rae"]["renderClass"] == "uniform_decal"
        assert shadow["alphaMode"] == "BLEND"
        assert shadow["doubleSided"] is True
        for wall_name in ("gs_pc_a", "gs_pc_a_", "gs_pc_b"):
            wall = by_name[wall_name]
            assert wall["extras"]["rae"]["renderClass"] == "opaque"
            assert "alphaMode" not in wall or wall.get("alphaMode") == "OPAQUE"
