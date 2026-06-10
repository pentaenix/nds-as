"""Tests for GLB material texture URI patching."""
from __future__ import annotations

from pathlib import Path

from rae.glb_policy.glb_io import GlbData, read_glb
from rae.glb_policy.texture_patch import (
    patch_glb_material_textures,
    write_flipbook_preview_glbs,
    write_patched_preview_glb,
)


def _write_minimal_glb(path: Path, *, materials: list[dict], images: list[dict] | None = None) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "materials": materials,
        "images": images or [{"uri": "base.png"}],
        "textures": [{"source": 0}],
    }
    for idx, material in enumerate(materials):
        if "pbrMetallicRoughness" not in material:
            material["pbrMetallicRoughness"] = {"baseColorTexture": {"index": 0}}
    GlbData(json=gltf, bin_chunk=b"").write(path)


def test_patch_glb_material_textures_rewrites_uri(tmp_path: Path) -> None:
    glb_path = tmp_path / "model.glb"
    _write_minimal_glb(glb_path, materials=[{"name": "Lamp"}])
    glb = read_glb(glb_path)
    patched = patch_glb_material_textures(glb, {"lamp": "lamp.2.png"})
    assert patched.json["images"][0]["uri"] == "lamp.2.png"


def test_write_patched_preview_glb_reclassifies_material(tmp_path: Path) -> None:
    glb_path = tmp_path / "model.glb"
    png_path = tmp_path / "lamp.2.png"
    png_path.write_bytes(b"\x89PNG\r\n")
    _write_minimal_glb(
        glb_path,
        materials=[
            {
                "name": "Lamp",
                "extras": {"rae": {"nitro": {"alpha": 0.4}, "renderClass": "uniform_decal"}},
            }
        ],
    )

    out_path = tmp_path / ".rae_preview" / "textured_00001.glb"
    write_patched_preview_glb(
        glb_path,
        out_path,
        mesh_labels=["Lamp"],
        mesh_texture_paths=[png_path],
    )
    patched = read_glb(out_path)
    material = patched.json["materials"][0]
    assert material["alphaMode"] == "BLEND"
    assert material["extras"]["rae"]["renderClass"] == "uniform_decal"
    factor = material["pbrMetallicRoughness"]["baseColorFactor"]
    assert factor[3] == 1.0


def test_write_patched_preview_glb_copies_png_and_patches(tmp_path: Path) -> None:
    glb_path = tmp_path / "model.glb"
    png_path = tmp_path / "lamp.2.png"
    png_path.write_bytes(b"\x89PNG\r\n")
    _write_minimal_glb(glb_path, materials=[{"name": "Lamp"}])

    out_path = tmp_path / ".rae_preview" / "textured_00001.glb"
    result = write_patched_preview_glb(
        glb_path,
        out_path,
        mesh_labels=["Lamp"],
        mesh_texture_paths=[png_path],
    )
    assert result == out_path
    assert out_path.is_file()
    assert (out_path.parent / "lamp.2.png").is_file()
    patched = read_glb(out_path)
    assert patched.json["images"][0]["uri"] == "lamp.2.png"


def test_write_flipbook_preview_glbs_writes_one_glb_per_frame(tmp_path: Path) -> None:
    glb_path = tmp_path / "model.glb"
    _write_minimal_glb(glb_path, materials=[{"name": "Lamp"}])
    frame_paths = []
    for idx in (1, 2, 3):
        path = tmp_path / f"lamp.{idx}.png"
        path.write_bytes(f"png{idx}".encode())
        frame_paths.append(path)

    out_dir = tmp_path / "flipbook"
    paths = write_flipbook_preview_glbs(
        glb_path,
        out_dir,
        material_name="Lamp",
        frame_paths=frame_paths,
        base_mesh_paths=[frame_paths[0]],
        mesh_labels=["Lamp"],
    )
    assert len(paths) == 3
    for frame_idx, path in enumerate(paths):
        assert path.name == f"frame_{frame_idx:03d}.glb"
        glb = read_glb(path)
        assert glb.json["images"][0]["uri"] == f"lamp.{frame_idx + 1}.png"
