from __future__ import annotations

from dataclasses import replace
import io
import json
from pathlib import Path
import struct
import zipfile
from xml.etree import ElementTree as ET

import numpy as np
import pytest
from PIL import Image

from rae.platforms.windows_iso.am import (
    Action, AnimationSet, Bone, Skeleton, decode_am1, decode_am2, decode_am3,
)
from rae.platforms.windows_iso.animated_dae import _human_t_pose_globals, write_animated_dae
from rae.platforms.windows_iso.animated_gltf import write_animated_glb
from rae.platforms.windows_iso.bulk_submission import (
    build_submission_jobs,
    human_submission_title,
    readable_submission_title,
    submission_category,
)
from rae.platforms.windows_iso.exporters import write_glb
from rae.platforms.windows_iso.smo import SmoMesh, decode_smo
from rae.platforms.windows_iso.service import PreparedModel, _fit_animation_bones, prepare_preview
from rae.platforms.windows_iso.submission import (
    export_submission,
    submission_camera,
    submission_output_directory,
    submission_title,
)
import rae.platforms.windows_iso.service as windows_service
import rae.platforms.windows_iso.submission as windows_submission
from rae.platforms.windows_iso.resources import model_bindings


pytestmark = pytest.mark.windows_iso


def _am1() -> bytes:
    data = bytearray(struct.pack("<5I4f", 500, 1, 1, 1, 0, 0, 0, 0, 1))
    data.extend(struct.pack("<II", 6, 3))
    data.extend(b"\0" * 64)
    # Eight indices begin at offset 0x48. The final two are stored after what
    # the old decoder incorrectly considered a 0x58-byte header.
    data.extend(struct.pack("<8H", 0, 0, 0, 0, 0, 1, 2, 2))
    data.extend(struct.pack("<2H", 2, 2))
    for index, position in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        data.extend(struct.pack("<2fIIIf6f", float(index == 1), float(index == 2), 0,
                                index, 0, 1.0, *position, 0, 0, 1))
    data.extend(b"test.bmp\0".ljust(24, b"\0"))
    return bytes(data)


def _am3() -> bytes:
    return struct.pack("<II50sI", 1, 0, b"root\0", 0)


def _am2() -> bytes:
    identity = (1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0)
    return b"".join((
        struct.pack("<3I", 1, 1, 0),
        struct.pack("<f58s", 1000.0, b"idle\0"),
        struct.pack("<fI", 1000.0, 2),
        struct.pack("<13f", 0.0, *identity),
        struct.pack("<13f", 1000.0, *identity),
    ))


def _glb_json(path: Path) -> dict:
    payload = path.read_bytes()
    json_size, kind = struct.unpack_from("<I4s", payload, 12)
    assert kind == b"JSON"
    return json.loads(payload[20:20 + json_size].rstrip(b" \0"))


def _resource(rows: list[tuple[str, str]]) -> bytes:
    table_end = 4 + len(rows) * 38
    body = bytearray()
    table = bytearray(struct.pack("<I", len(rows)))
    for key, text in rows:
        table.extend(key.encode("ascii").ljust(34, b"\0"))
        table.extend(struct.pack("<I", table_end + len(body)))
        body.extend(text.encode("ascii"))
    return bytes(table + body)


def _smo_v500(*, mesh_count: int = 1, material_slots: int = 1,
              texture_name: bytes = b"test.bmp") -> bytes:
    data = bytearray(struct.pack("<8I", 500, mesh_count, material_slots, 0, 0, 0, 0, 0))
    for _ in range(mesh_count):
        data.extend(struct.pack("<II", 0, 3))
        data.extend(b"\0" * 80)
        data.extend(struct.pack("<4H", 0, 1, 2, 2))
        for index, position in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
            data.extend(struct.pack(
                "<8f", 0, 0, 1, *position, float(index == 1), float(index == 2),
            ))
    data.extend(texture_name + b"\0")
    return bytes(data)


def test_decodes_and_writes_skinned_animated_models(tmp_path: Path) -> None:
    mesh = decode_am1(_am1())
    skeleton = decode_am3(_am3())
    animation = decode_am2(_am2())
    assert mesh.faces == ((0, 1, 2),)
    assert mesh.uvs == ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    assert skeleton.bones[0].name == "root"
    assert animation.clips() == (("idle", 0.0, 1000.0),)

    glb = write_animated_glb(mesh, skeleton, [animation, animation], tmp_path / "model.glb")
    document = _glb_json(glb)
    assert len(document["skins"][0]["joints"]) == 1
    assert [row["name"] for row in document["animations"]] == ["idle", "idle_2"]
    assert document["asset"]["extras"]["rae"]["platform"] == "windows_iso"

    dae = write_animated_dae(mesh, skeleton, [animation, animation], tmp_path / "model.dae")
    root = ET.parse(dae).getroot()
    namespace = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    assert len(root.findall(".//c:controller", namespace)) == 1
    assert len(root.findall(".//c:animation_clip", namespace)) == 2
    assert len(root.findall('.//c:node[@type="JOINT"]', namespace)) == 1
    uv_array = root.find('.//c:source[@id="uvs"]/c:float_array', namespace)
    assert uv_array is not None and uv_array.text is not None
    assert [float(value) for value in uv_array.text.split()] == [0.0, 1.0, 1.0, 1.0, 0.0, 0.0]


def test_decodes_legacy_smo_triangle_list(tmp_path: Path) -> None:
    data = bytearray(struct.pack("<4I", 50, 1, 0, 0))
    data.extend(struct.pack("<II", 1, 3))
    data.extend(b"\0" * 40)
    data.extend(struct.pack("<3H", 0, 1, 2))
    for index, position in enumerate(((0, 0, 0), (1, 0, 0), (0, 1, 0))):
        data.extend(struct.pack(
            "<8f", 0, 0, 1, *position, float(index == 1), float(index == 2),
        ))
    mesh = decode_smo(bytes(data))
    assert mesh.faces == ((0, 1, 2),)
    assert mesh.uvs == ((0.0, 0.0), (1.0, 0.0), (0.0, 1.0))
    assert "legacy SMO v50" in mesh.warnings[0]
    document = _glb_json(write_glb(mesh, tmp_path / "legacy.glb"))
    assert document["meshes"][0]["primitives"][0]["attributes"]["POSITION"] == 0


def test_decodes_shared_truncated_smo_texture_reference_without_warning() -> None:
    mesh = decode_smo(_smo_v500(
        mesh_count=2,
        material_slots=1,
        texture_name=b"animal_display2_1.TG",
    ))
    assert mesh.texture_names == ("animal_display2_1.TG",)
    assert mesh.material_slots == 2
    assert set(mesh.face_materials) == {0, 1}
    assert mesh.warnings == ()


def test_adapts_source_game_one_bone_animation_variants() -> None:
    animation = decode_am2(_am2())
    expanded = _fit_animation_bones(animation, 2)
    assert expanded is not None and len(expanded.tracks) == 2
    assert expanded.tracks[-1][0].time_ms == 0.0
    assert _fit_animation_bones(animation, 3) is None


def test_am2_action_names_precede_their_stored_end_marker() -> None:
    animation = AnimationSet(
        actions=(
            Action("idle", 9000.0),
            Action("walk", 3000.0),
            Action("run", 6000.0),
        ),
        duration_ms=9000.0,
        tracks=(),
    )
    assert animation.clips() == (
        ("idle", 0.0, 3000.0),
        ("walk", 3000.0, 6000.0),
        ("run", 6000.0, 9000.0),
    )


def test_am2_accepts_known_auxiliary_footer_but_rejects_unknown_tail() -> None:
    footer = struct.pack("<6I", 1, 1, 0, 0, 0, 0)
    assert decode_am2(_am2() + footer).clips() == (("idle", 0.0, 1000.0),)
    with pytest.raises(ValueError, match="unexplained trailing bytes"):
        decode_am2(_am2() + b"unknown")


def test_links_authoritative_animation_sets_and_shadows() -> None:
    models = _resource([
        ("ELE_AFR", "ANIM_GROUP: AGEle\r\nSIMPLE_SHADOW: Shadow.smo\r\nDETAIL_SHADOW: ele#s.am1\r\n"),
        ("BUTTERFLY", "AM2: Butterflyfish\r\nSIMPLE_SHADOW: NO SHADOW\r\n"),
    ])
    groups = _resource([("AGELE", "AM2_NAME: elephant\r\n~\r\nAM2_NAME: elephant2\r\n")])
    binding = model_bindings(models, groups)["ELE_AFR"]
    assert binding.animation_stems == ("elephant", "elephant2")
    assert binding.simple_shadow == "Shadow.smo"
    assert binding.detail_shadow == "ele#s.am1"
    assert model_bindings(models, groups)["BUTTERFLY"].animation_stems == ("Butterflyfish",)


def _triangle_mesh() -> SmoMesh:
    return SmoMesh(
        vertices=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        normals=((0.0, 0.0, 1.0),) * 3,
        uvs=((0.0, 0.0), (1.0, 0.0), (0.0, 1.0)),
        faces=((0, 1, 2),),
        face_materials=(0,),
        texture_names=("test.png",),
        material_slots=1,
    )


@pytest.mark.parametrize(
    ("alpha", "expected_mode", "expected_class"),
    [
        ([255, 255, 255, 255], None, "opaque"),
        ([0, 255, 0, 255], "MASK", "mask"),
        ([0, 96, 192, 255], "BLEND", "alpha_blend"),
    ],
)
def test_glb_uses_texture_alpha_instead_of_forcing_blend(
    tmp_path: Path,
    alpha: list[int],
    expected_mode: str | None,
    expected_class: str,
) -> None:
    texture = tmp_path / "texture.png"
    image = Image.new("RGBA", (2, 2), (255, 255, 255, 255))
    image.putalpha(Image.frombytes("L", (2, 2), bytes(alpha)))
    image.save(texture)
    document = _glb_json(write_glb(
        _triangle_mesh(), tmp_path / "model.glb", texture_pngs=[texture],
    ))
    material = document["materials"][0]
    assert material.get("alphaMode") == expected_mode
    assert material["extras"]["rae"]["renderClass"] == expected_class


def test_glb_classifies_shared_texture_alpha_per_mesh_region(tmp_path: Path) -> None:
    texture = tmp_path / "shared.png"
    pixels = Image.new("RGBA", (16, 4), (255, 255, 255, 255))
    for x in range(8):
        for y in range(4):
            pixels.putpixel((x, y), (255, 255, 255, 170))
    pixels.save(texture)
    mesh = SmoMesh(
        vertices=(
            (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0),
            (2.0, 0.0, 0.0), (3.0, 0.0, 0.0), (2.0, 1.0, 0.0),
        ),
        normals=((0.0, 0.0, 1.0),) * 6,
        uvs=((0.05, 0.1), (0.4, 0.1), (0.05, 0.9),
             (0.6, 0.1), (0.95, 0.1), (0.6, 0.9)),
        faces=((0, 1, 2), (3, 4, 5)),
        face_materials=(0, 1),
        texture_names=("shared.png",),
        material_slots=2,
    )
    document = _glb_json(write_glb(
        mesh, tmp_path / "regions.glb", texture_pngs=[texture],
    ))
    translucent, opaque = document["materials"]
    assert translucent["alphaMode"] == "BLEND"
    assert translucent["extras"]["rae"]["renderClass"] == "alpha_blend"
    assert "alphaMode" not in opaque
    assert opaque["extras"]["rae"]["renderClass"] == "opaque"
    assert opaque["pbrMetallicRoughness"]["baseColorFactor"] == [1.0, 1.0, 1.0, 1.0]


def test_glb_ignores_mostly_opaque_animal_surface_alpha(tmp_path: Path) -> None:
    texture = tmp_path / "animal.png"
    pixels = Image.new("RGBA", (16, 16), (40, 110, 150, 255))
    for y in range(16):
        pixels.putpixel((1, y), (40, 110, 150, 168))
    pixels.save(texture)
    mesh = replace(
        decode_am1(_am1()),
        uvs=((0.08, 0.1), (0.8, 0.1), (0.8, 0.9)),
    )
    document = _glb_json(write_animated_glb(
        mesh,
        decode_am3(_am3()),
        decode_am2(_am2()),
        tmp_path / "animal.glb",
        texture_pngs=[texture],
    ))
    material = document["materials"][0]
    assert "alphaMode" not in material
    assert material["extras"]["rae"]["renderClass"] == "opaque"


def test_animated_mesh_slots_share_the_single_declared_texture(tmp_path: Path) -> None:
    texture = tmp_path / "animal.png"
    Image.new("RGBA", (2, 2), (80, 120, 160, 255)).save(texture)
    mesh = replace(decode_am1(_am1()), face_materials=(1,))
    document = _glb_json(write_animated_glb(
        mesh,
        decode_am3(_am3()),
        decode_am2(_am2()),
        tmp_path / "animal.glb",
        texture_pngs=[texture],
    ))
    assert "baseColorTexture" in document["materials"][0]["pbrMetallicRoughness"]
    assert "baseColorTexture" in document["materials"][1]["pbrMetallicRoughness"]
    dae = write_animated_dae(
        mesh,
        decode_am3(_am3()),
        decode_am2(_am2()),
        tmp_path / "animal.dae",
        texture_filenames=[texture.name],
    )
    root = ET.parse(dae).getroot()
    namespace = {"c": "http://www.collada.org/2005/11/COLLADASchema"}
    second_image = root.find('.//c:image[@id="image-1"]/c:init_from', namespace)
    assert second_image is not None and second_image.text == texture.name


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("model/animal/manateeS.AM1", "Manatee Baby"),
        ("model/animal/orcaF.AM1", "Orca Female"),
        ("model/animal/spinner_dolphin.AM1", "Spinner Dolphin"),
        ("model/animal/snow leopardS.AM1", "Snow Leopard Baby"),
        ("model/animal/seaotter_baby.AM1", "Seaotter Baby"),
        ("model/animal/RareBear.AM1", "Rare Bear"),
        ("model/animal/RareBearS.AM1", "Rare Bear Baby"),
        ("model/animal/nzs_female.AM1", "New Zealand Sea Lion Female"),
        ("model/animal/nz_sealion_baby.AM1", "New Zealand Sea Lion Baby"),
        ("model/animal/oceanf.AM1", "Oceanf"),
    ],
)
def test_models_resource_submission_titles(source: str, expected: str) -> None:
    assert submission_title(source) == expected


def test_models_resource_submission_camera_uses_animal_side_profile() -> None:
    assert submission_camera("model/animal/orca.AM1") == (0.0, 14.0, 0.84)
    assert submission_camera("model/item/cafe.SMO") == (30.0, 27.0, 0.82)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("model/human/VAFNormal01.AM1", "Visitor Adult Female Normal 01"),
        ("model/human/VAFTall.AM1", "Visitor Adult Female Tall"),
        ("model/human/VAMaleTall02-bind.AM1", "Visitor Adult Male Tall 02"),
        ("model/human/VisitorAdultMaleNormal03.AM1", "Visitor Adult Male Normal 03"),
        ("model/human/VKMShort01_bind.AM1", "Visitor Kid Male Short 01"),
        ("model/human/vomnormal01_bind.AM1", "Visitor Old Normal 01"),
        ("model/human/PlayerCharacterF.AM1", "Player Character Female"),
        ("model/human/VOFN103-bind.AM1", "Visitor Old Female Normal 03"),
    ],
)
def test_human_submission_titles(source: str, expected: str) -> None:
    assert human_submission_title(source) == expected


@pytest.mark.parametrize(
    ("source", "format_name", "expected"),
    [
        ("model/animal/orca.AM1", "AM1", "animals"),
        ("model/decor/Rock.SMO", "SMO", "decor"),
        ("model/item/show_seal.AM1", "AM1", "show animals"),
        ("model/item/Fence01_D.AM1", "AM1", "fences"),
        ("model/item/angelfish.AM1", "AM1", "ambient animals"),
        ("model/item/Swing.SMO", "SMO", "items"),
        ("model/misc/butterfly.AM1", "AM1", "ambient animals"),
        ("model/misc/Car-001a.SMO", "SMO", "vehicles"),
        ("model/Train/Train.SMO", "SMO", "vehicles"),
        ("model/Shadow/blob.SMO", "SMO", None),
        ("model/holylight.SMO", "SMO", None),
    ],
)
def test_bulk_submission_categories(source: str, format_name: str, expected: str | None) -> None:
    assert submission_category({"model": {"path": source, "format": format_name}}) == expected


def test_bulk_jobs_prefer_full_human_over_binding_duplicate() -> None:
    binding = {"model": {
        "path": "model/human/VAMaleTall02-bind.AM1", "format": "AM1", "size": 999,
    }}
    full = {"model": {
        "path": "model/human/VisitorAdultMaleTall02.AM1", "format": "AM1", "size": 1,
    }}
    jobs = build_submission_jobs([binding, full])
    assert len(jobs) == 1
    assert jobs[0].title == "Visitor Adult Male Tall 02"
    assert jobs[0].descriptor is full
    assert jobs[0].dae_human_t_pose is True


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("ele_afr", "African Elephant"),
        ("hsharkS", "Hammerhead Shark Baby"),
        ("K_Dragon", "Komodo Dragon"),
        ("wshark", "Great White Shark"),
        ("RedDeerM", "Red Deer Male"),
    ],
)
def test_animal_submission_title_overrides(stem: str, expected: str) -> None:
    descriptor = {"model": {"path": f"model/animal/{stem}.AM1", "format": "AM1"}}
    assert readable_submission_title(descriptor) == expected


def test_generated_human_bind_pose_straightens_arms_horizontally() -> None:
    names = [f"bone {index}" for index in range(16)]
    names[7:11] = [
        "Bip01 L UpperArm", "Bip01 L Forearm", "Bip01 L Hand", "Bip01 L Finger0",
    ]
    names[12:16] = [
        "Bip01 R UpperArm", "Bip01 R Forearm", "Bip01 R Hand", "Bip01 R Finger0",
    ]
    skeleton = Skeleton(
        tuple(Bone(index, name, ()) for index, name in enumerate(names)),
        tuple(None for _ in names),
    )
    matrices = [np.eye(4) for _ in names]
    for indices, lateral in (((7, 8, 9, 10), 1.0), ((12, 13, 14, 15), -1.0)):
        for step, index in enumerate(indices):
            matrices[index][:3, 3] = (0.0, lateral * step, 10.0 - step * 3.0)
    posed = _human_t_pose_globals(skeleton, matrices)
    assert [round(float(posed[index][2, 3]), 6) for index in (7, 8, 9, 10)] == [10.0] * 4
    assert posed[10][1, 3] > posed[7][1, 3]
    assert posed[15][1, 3] < posed[12][1, 3]


def test_models_resource_submission_layout_and_dimensions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    descriptor = {
        "game_title": "Marine Park Empire",
        "model": {"path": "model/animal/orca.AM1", "format": "AM1"},
    }

    def fake_prepare(
        descriptor, output_dir, *, include_dae, dae_human_t_pose, progress,
    ):
        del descriptor, include_dae, dae_human_t_pose, progress
        output_dir = Path(output_dir)
        dae = output_dir / "orca.dae"
        texture = output_dir / "orca.png"
        glb = output_dir / "orca_preview.glb"
        dae.write_text(
            """<?xml version="1.0" encoding="utf-8"?>
<COLLADA xmlns="http://www.collada.org/2005/11/COLLADASchema" version="1.4.1">
  <library_images><image><init_from>orca.png</init_from></image></library_images>
  <library_geometries><geometry id="geometry"/></library_geometries>
  <library_controllers><controller id="skin"/></library_controllers>
  <library_animation_clips>
    <animation_clip name="orca_idle"/><animation_clip name="orca_swim"/>
  </library_animation_clips>
</COLLADA>
""",
            encoding="utf-8",
        )
        Image.new("RGBA", (8, 8), (255, 255, 255, 255)).save(texture)
        document = {
            "asset": {"version": "2.0"},
            "meshes": [{}],
            "skins": [{}],
            "animations": [{"name": "orca_idle"}, {"name": "orca_swim"}],
        }
        encoded = json.dumps(document, separators=(",", ":")).encode("utf-8")
        encoded += b" " * ((-len(encoded)) % 4)
        body = struct.pack("<I4s", len(encoded), b"JSON") + encoded
        glb.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
        return PreparedModel(
            source_model=output_dir / "orca.AM1",
            mesh=_triangle_mesh(),
            texture_pngs=(texture,),
            glb_path=glb,
            dae_path=dae,
            warnings=(),
            animation_names=("orca_idle", "orca_swim"),
        )

    def snapshot(_path: Path) -> bytes:
        image = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
        for x in range(40, 360):
            for y in range(80, 240):
                image.putpixel((x, y), (20, 30, 40, 255))
        stream = io.BytesIO()
        image.save(stream, format="PNG")
        return stream.getvalue()

    monkeypatch.setattr(windows_submission, "prepare_model", fake_prepare)
    output = submission_output_directory(tmp_path, descriptor)
    written = export_submission(descriptor, output, snapshot)
    assert [path.name for path in written] == [
        "Orca.zip",
        "Orca_icon.png",
        "Orca_preview.glb",
        "Orca_preview.png",
    ]
    with zipfile.ZipFile(output / "Orca.zip") as archive:
        assert archive.namelist() == ["Orca.dae", "orca.png"]
    with Image.open(output / "Orca_icon.png") as icon:
        assert icon.mode == "RGBA" and icon.size == (148, 125)
        assert icon.getpixel((0, 0))[3] == 0
    with Image.open(output / "Orca_preview.png") as preview:
        assert preview.mode == "RGBA" and preview.size == (750, 650)
        assert preview.getpixel((0, 0))[3] == 0
    assert (output / "Orca_preview.glb").read_bytes().startswith(b"glTF")
    assert submission_output_directory(output, descriptor) == output


def test_interactive_preview_reuses_cache_and_limits_animation_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def fake_prepare(
        descriptor, output_dir, *, include_dae, animation_scope,
        animation_clip_limit, progress,
    ):
        calls.append(f"{animation_scope}:{animation_clip_limit}")
        output = Path(output_dir) / "Jaguar_preview.glb"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(struct.pack("<4sII", b"glTF", 2, 12))
        return PreparedModel(
            source_model=tmp_path / "Jaguar.AM1",
            mesh=_triangle_mesh(),
            texture_pngs=(),
            glb_path=output,
            dae_path=None,
            warnings=("fixture warning",),
        )

    monkeypatch.setattr(windows_service, "prepare_model", fake_prepare)
    descriptor = {
        "model": {"path": "model/animal/Jaguar.AM1", "format": "AM1", "size": 100},
        "animations": [{"path": "anim/Panther.am2", "format": "AM2", "size": 200}],
    }
    first = prepare_preview(descriptor, tmp_path / "preview")
    second = prepare_preview(descriptor, tmp_path / "preview")
    complete = prepare_preview(
        descriptor, tmp_path / "complete", full_animations=True,
    )
    complete_cached = prepare_preview(
        descriptor, tmp_path / "complete", full_animations=True,
    )
    assert first.glb_path == second.glb_path
    assert second.warnings == ("fixture warning",)
    assert complete.glb_path == complete_cached.glb_path
    assert calls == ["primary:4", "all:None"]
