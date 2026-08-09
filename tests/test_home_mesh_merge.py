"""HOME multi-mesh models: merging BodySkin + part meshes into one preview GLB."""
from __future__ import annotations

from pathlib import Path

from rae.platforms.home.assetstudio_preview import _sibling_form_meshes
from rae.platforms.mobile.mesh_export import _merge_obj_models, _parse_obj, _split_obj_by_groups

_BODY_OBJ = """\
v 0 0 0
v 1 0 0
v 0 1 0
vt 0 0
vt 1 0
vt 0 1
g pm0012_00_00_BodySkin_0
f 1/1 2/2 3/3
"""

_WING_OBJ = """\
v 2 0 0
v 3 0 0
v 2 1 0
vt 0 0
vt 1 0
vt 0 1
g pm0012_00_00_FeelerSkin_0
f 1/1 2/2 3/3
"""


def test_merge_obj_models_preserves_groups_and_offsets_faces():
    body = _parse_obj(_BODY_OBJ)
    wing = _parse_obj(_WING_OBJ)
    merged = _merge_obj_models([("pm0012_00_00_BodySkin", body), ("pm0012_00_00_FeelerSkin", wing)])

    assert len(merged.positions) == 6
    assert len(merged.faces) == 2
    # Second mesh's face indices must be offset past the first mesh's vertices.
    assert merged.faces[1] == (3, 4, 5)

    parts = dict(_split_obj_by_groups(merged))
    assert "pm0012_00_00_BodySkin_0" in parts
    assert "pm0012_00_00_FeelerSkin_0" in parts


def test_merge_obj_models_namespaces_clashing_groups():
    a = _parse_obj(_BODY_OBJ)
    b = _parse_obj(_BODY_OBJ)
    merged = _merge_obj_models([("MeshA", a), ("MeshB", b)])
    groups = set(merged.face_groups)
    assert "pm0012_00_00_BodySkin_0" in groups
    assert "MeshB__pm0012_00_00_BodySkin_0" in groups


def test_sibling_form_meshes_only_matches_same_form():
    objs = [
        Path("/tmp/x/pm0012_00_00_BodySkin.obj"),
        Path("/tmp/x/pm0012_00_00_FeelerSkin.obj"),
        Path("/tmp/x/pm0012_01_00_FeelerSkin.obj"),
        Path("/tmp/x/pm0999_00_00_BodySkin.obj"),
    ]
    siblings = _sibling_form_meshes(objs[0], objs)
    names = [p.stem for p in siblings]
    assert names[0] == "pm0012_00_00_BodySkin"
    assert "pm0012_00_00_FeelerSkin" in names
    assert "pm0012_01_00_FeelerSkin" not in names
    assert "pm0999_00_00_BodySkin" not in names
