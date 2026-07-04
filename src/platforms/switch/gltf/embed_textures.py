"""Embed external glTF image URIs into the GLB binary buffer."""
from __future__ import annotations

import copy
from pathlib import Path

from .glb_io import GlbData
from .texture_patch import _texture_search_index


def _mime_type_for_image(data: bytes, uri: str) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    ext = Path(uri).suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        return "image/jpeg"
    return "image/png"


def _resolve_image_path(
    uri: str,
    *,
    base_dir: Path,
    search_index: dict[str, Path],
) -> Path | None:
    uri = str(uri or "").strip()
    if not uri or uri.startswith("data:"):
        return None
    candidates = [
        base_dir / uri,
        base_dir / Path(uri).name,
        search_index.get(Path(uri).name.casefold()),
    ]
    for candidate in candidates:
        if candidate is not None and Path(candidate).is_file():
            return Path(candidate)
    return None


def embed_glb_external_images(
    glb: GlbData,
    *,
    base_dir: Path,
    search_paths: list[Path] | None = None,
    require_all: bool = False,
) -> GlbData:
    """Copy every external ``images[].uri`` PNG/JPEG into the GLB BIN chunk."""
    base_dir = base_dir.resolve()
    search_index = _texture_search_index([base_dir, *(search_paths or [])])
    gltf = copy.deepcopy(glb.json)
    bin_chunk = bytearray(glb.bin_chunk)

    buffers = list(gltf.get("buffers") or [])
    if not buffers:
        buffers = [{"byteLength": len(bin_chunk)}]
    elif isinstance(buffers[0], dict):
        buffers[0] = dict(buffers[0])
        buffers[0].pop("uri", None)
        buffers[0]["byteLength"] = len(bin_chunk)
    else:
        buffers = [{"byteLength": len(bin_chunk)}]

    buffer_views = list(gltf.get("bufferViews") or [])
    images = list(gltf.get("images") or [])
    uri_to_view: dict[str, int] = {}
    missing: list[str] = []

    for img_idx, image in enumerate(images):
        if not isinstance(image, dict):
            continue
        if image.get("bufferView") is not None:
            continue
        uri = str(image.get("uri") or "").strip()
        if not uri or uri.startswith("data:"):
            continue

        if uri in uri_to_view:
            images[img_idx] = {
                "mimeType": image.get("mimeType") or _mime_type_for_image(b"", uri),
                "bufferView": uri_to_view[uri],
            }
            continue

        path = _resolve_image_path(uri, base_dir=base_dir, search_index=search_index)
        if path is None:
            missing.append(uri)
            continue

        data = path.read_bytes()
        while len(bin_chunk) % 4:
            bin_chunk.append(0)
        offset = len(bin_chunk)
        bin_chunk.extend(data)
        while len(bin_chunk) % 4:
            bin_chunk.append(0)

        view_idx = len(buffer_views)
        buffer_views.append(
            {
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(data),
            }
        )
        mime = _mime_type_for_image(data, uri)
        uri_to_view[uri] = view_idx
        images[img_idx] = {"mimeType": mime, "bufferView": view_idx}

    if require_all and missing:
        joined = ", ".join(missing[:8])
        suffix = f" (+{len(missing) - 8} more)" if len(missing) > 8 else ""
        raise RuntimeError(
            f"Export GLB still references external texture(s) that could not be embedded: {joined}{suffix}"
        )

    if isinstance(buffers[0], dict):
        buffers[0]["byteLength"] = len(bin_chunk)

    gltf["buffers"] = buffers
    gltf["bufferViews"] = buffer_views
    gltf["images"] = images
    return GlbData(json=gltf, bin_chunk=bytes(bin_chunk))
