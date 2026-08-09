"""Tests for embedding external GLB textures into the BIN chunk."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from rae.platforms.nds.gltf.embed_textures import embed_glb_external_images
from rae.platforms.nds.gltf.glb_io import GlbData, read_glb


def _write_textured_glb(path: Path, png_name: str) -> None:
    gltf = {
        "asset": {"version": "2.0"},
        "materials": [{"name": "Sand", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}],
        "images": [{"uri": png_name}],
        "textures": [{"source": 0}],
        "buffers": [{"byteLength": 0}],
    }
    GlbData(json=gltf, bin_chunk=b"").write(path)


def test_embed_glb_external_images(tmp_path: Path) -> None:
    png_path = tmp_path / "sand.png"
    Image.new("RGBA", (4, 4), (200, 160, 80, 255)).save(png_path)
    glb_path = tmp_path / "terrain.glb"
    _write_textured_glb(glb_path, "sand.png")

    glb = read_glb(glb_path)
    embedded = embed_glb_external_images(glb, base_dir=tmp_path, require_all=True)

    assert embedded.json["images"][0].get("bufferView") is not None
    assert "uri" not in embedded.json["images"][0]
    assert len(embedded.bin_chunk) > 0
    assert embedded.json["buffers"][0]["byteLength"] == len(embedded.bin_chunk)


def test_embed_glb_external_images_deduplicates_shared_uri(tmp_path: Path) -> None:
    png_path = tmp_path / "shared.png"
    Image.new("RGBA", (2, 2), (255, 0, 0, 255)).save(png_path)
    gltf = {
        "asset": {"version": "2.0"},
        "materials": [
            {"name": "A", "pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}},
            {"name": "B", "pbrMetallicRoughness": {"baseColorTexture": {"index": 1}}},
        ],
        "images": [{"uri": "shared.png"}, {"uri": "shared.png"}],
        "textures": [{"source": 0}, {"source": 1}],
        "buffers": [{"byteLength": 0}],
    }
    glb_path = tmp_path / "model.glb"
    GlbData(json=gltf, bin_chunk=b"").write(glb_path)

    embedded = embed_glb_external_images(read_glb(glb_path), base_dir=tmp_path, require_all=True)
    views = embedded.json["bufferViews"]
    image_views = [image["bufferView"] for image in embedded.json["images"]]
    assert image_views[0] == image_views[1]
    assert len(views) == 1
