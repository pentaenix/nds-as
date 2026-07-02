from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from ...core.texture_assignments import texture_key_for_path
from ...core.preview.texture_paths import texture_map_from_paths

_TEXTURE_SUFFIX_RE = re.compile(r"_(col|emi|nrm|spm|mask|ao|met|rough)(?:_\d+)?$", re.IGNORECASE)
_MESH_PART_SUFFIXES = ("skin", "mesh", "geo", "lod")
_SPECIES_ID_RE = re.compile(r"^pm\d{4}_\d{2}_\d{2}$", re.IGNORECASE)
# AssetStudio HOME exports: BodySkin_3 (and sometimes _2) carry eye geometry.
_HOME_EYE_GROUP_SUFFIXES = frozenset({"2", "3"})


@dataclass(slots=True)
class HomeTextureBindings:
    texture_paths: list[Path] = field(default_factory=list)
    texture_by_name: dict[str, Path] = field(default_factory=dict)
    material_to_texture: dict[str, str] = field(default_factory=dict)
    texture_bind_order: list[str] = field(default_factory=list)
    fallback_paths: list[Path] = field(default_factory=list)


def export_home_textures(
    source: str | Path,
    output_dir: str | Path,
    *,
    name_filter: str | None = None,
) -> list[Path]:
    """Export Texture2D assets from a HOME bundle or Cache folder via AssetStudio."""
    bundle = Path(source).expanduser().resolve()
    if not bundle.exists():
        raise FileNotFoundError(bundle)

    out_root = Path(output_dir).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    args = [str(bundle), "-m", "export", "-t", "Texture2D", "-o", str(out_root)]
    if name_filter:
        args.extend(["--filter-by-name", name_filter])
    from .assetstudio_preview import _run_assetstudio

    _run_assetstudio(args)

    paths = sorted(
        (
            path
            for path in out_root.rglob("*")
            if path.is_file() and path.suffix.casefold() in {".png", ".bmp", ".tga", ".jpg", ".jpeg"}
        ),
        key=lambda path: path.name.casefold(),
    )
    return paths


def stage_textures_beside_glb(texture_paths: list[Path], glb_path: Path) -> list[Path]:
    """Copy exported textures next to the preview GLB for colocated fallback lookup."""
    glb_path = glb_path.expanduser().resolve()
    glb_path.parent.mkdir(parents=True, exist_ok=True)
    staged: list[Path] = []
    seen: set[str] = set()
    for source in texture_paths:
        if not source.is_file():
            continue
        target = glb_path.parent / source.name
        key = target.name.casefold()
        if key in seen:
            continue
        if not target.exists() or target.stat().st_size != source.stat().st_size:
            shutil.copy2(source, target)
        staged.append(target.resolve())
        seen.add(key)
    return staged


def build_home_texture_bindings(
    mesh_name: str,
    texture_paths: list[Path],
    *,
    part_names: list[str] | None = None,
) -> HomeTextureBindings:
    """Map HOME mesh names (BodySkin, Eye, …) to exported _col/_emi texture PNGs."""
    paths = [path.expanduser().resolve() for path in texture_paths if path.is_file()]
    texture_by_name = texture_map_from_paths(paths)

    col_paths = [path for path in paths if _texture_kind(path.stem) == "col"]
    bind_order = [_texture_key(path) for path in sorted(col_paths or paths, key=lambda p: p.name.casefold())]

    material_to_texture: dict[str, str] = {}
    mesh_keys: list[str] = []
    seen_keys: set[str] = set()
    for name in list(part_names or []) + [mesh_name]:
        for mesh_key in _material_binding_keys(name):
            norm = mesh_key.casefold()
            if norm in seen_keys:
                continue
            seen_keys.add(norm)
            mesh_keys.append(mesh_key)

    for mesh_key in mesh_keys:
        match = _best_texture_for_mesh(mesh_key, paths)
        if match is not None:
            tex_key = _texture_key(match)
            for alias in _material_binding_keys(mesh_key):
                material_to_texture[alias.casefold()] = tex_key

    return HomeTextureBindings(
        texture_paths=paths,
        texture_by_name=texture_by_name,
        material_to_texture=material_to_texture,
        texture_bind_order=bind_order,
        fallback_paths=col_paths or paths,
    )


def align_home_bindings_to_glb(
    glb_path: Path,
    bindings: HomeTextureBindings,
    *,
    texture_paths: list[Path] | None = None,
) -> HomeTextureBindings:
    """Bind every glTF material of a HOME export to the right _col texture.

    HOME "BodySkin" meshes hide the eye geometry in anonymous numbered submeshes
    (BodySkin_2, Skin_3, …) that differ per species, so name-based guessing puts
    the mostly-white eye sheet on body parts. Instead, classify each primitive
    geometrically: eyes are tiny (few verts, small 3D extent) and their UVs land
    on the eye sheet's drawn features. Everything else gets a body _col texture.
    """
    paths = [path.expanduser().resolve() for path in (texture_paths or bindings.texture_paths) if path.is_file()]
    material_to_texture = dict(bindings.material_to_texture)

    try:
        geometry_bindings = _bind_textures_by_geometry(glb_path, paths)
    except Exception:
        geometry_bindings = {}
    if geometry_bindings:
        # Geometry-driven bindings are authoritative for the numbered submeshes.
        for material, tex_key in geometry_bindings.items():
            for alias in _material_binding_keys(material):
                material_to_texture[alias.casefold()] = tex_key
    else:
        from ...glb_policy.preview_textures import parse_glb_mesh_parts

        for part in parse_glb_mesh_parts(glb_path):
            label = str(part.label or "").strip()
            if not label:
                continue
            for mesh_key in _material_binding_keys(label):
                key = mesh_key.casefold()
                if key in material_to_texture:
                    continue
                match = _best_texture_for_mesh(mesh_key, paths)
                if match is None:
                    continue
                tex_key = _texture_key(match)
                for alias in _material_binding_keys(mesh_key):
                    material_to_texture[alias.casefold()] = tex_key

    staged_paths = list(bindings.texture_paths)
    texture_by_name = dict(bindings.texture_by_name)
    if paths:
        texture_by_name = texture_map_from_paths(paths)
    return HomeTextureBindings(
        texture_paths=staged_paths,
        texture_by_name=texture_by_name,
        material_to_texture=material_to_texture,
        texture_bind_order=list(bindings.texture_bind_order),
        fallback_paths=list(bindings.fallback_paths or col_paths_from(paths)),
    )


# Eye submeshes: at most this share of total vertices / model bounding diagonal,
# with UV samples hitting at least this fraction of drawn eye-sheet features.
_EYE_MAX_VERT_SHARE = 0.08
_EYE_MAX_BBOX_DIAG = 0.30
_EYE_MIN_FEATURE_HIT = 0.08


def _bind_textures_by_geometry(glb_path: Path, texture_paths: list[Path]) -> dict[str, str]:
    """Per-material texture keys decided from GLB geometry + UV/texture analysis."""
    try:
        prims = _read_glb_primitives(glb_path)
    except Exception:
        return {}
    if not prims:
        return {}

    col_paths = [p for p in texture_paths if _texture_kind(p.stem) == "col"] or list(texture_paths)
    eye_paths = _dedupe_shiny_variants([p for p in col_paths if "eye" in p.stem.casefold()])
    body_paths = sorted(
        _dedupe_shiny_variants([p for p in col_paths if "eye" not in p.stem.casefold()]),
        key=lambda p: p.name.casefold(),
    )
    if not body_paths:
        body_paths = list(col_paths)

    eye_features = _texture_feature_mask(eye_paths[0]) if eye_paths else None
    body_masks = [(path, _texture_pixels(path), _painted_mask(path)) for path in body_paths]

    total_verts = sum(len(prim["uv"]) for prim in prims) or 1
    model_diag = _bbox_diag([p for prim in prims for p in prim["pos_bounds"]])

    out: dict[str, str] = {}
    for prim in prims:
        share = len(prim["uv"]) / total_verts
        diag = _bbox_diag(prim["pos_bounds"]) / model_diag if model_diag > 0 else 1.0
        is_eye = False
        if eye_features is not None and share <= _EYE_MAX_VERT_SHARE and diag <= _EYE_MAX_BBOX_DIAG:
            is_eye = _uv_feature_hit(eye_features, prim["uv"]) >= _EYE_MIN_FEATURE_HIT
        if is_eye and eye_paths:
            out[prim["material"]] = _texture_key(eye_paths[0])
        elif body_masks:
            # Multi-sheet bodies (BodyA/BodyB/…): pick the sheet whose painted
            # regions best coincide with this primitive's UV islands (IoU),
            # penalizing sheets whose texels are incoherent inside the UV
            # triangles (a UV/sheet mismatch bleeds unrelated art across faces).
            cover = _uv_coverage_mask(prim["uv"], prim["faces"])
            scored = []
            for path, pixels, painted in body_masks:
                iou = _mask_iou(cover, painted)
                inc = _triangle_incoherence(pixels, prim["uv"], prim["faces"])
                scored.append((iou - 0.5 * inc, iou, inc, path))
            scored.sort(key=lambda item: -item[0])
            _, best_iou, best_inc, best_path = scored[0]
            # Coherence gate for the main body mesh: skin prims sit on flat
            # base colors, so a winner that samples incoherently while another
            # sheet reads clean is a coincidental-overlap misfire (e.g. a body
            # UV island sprawled across the wing sheet's art).
            is_main_skin = re.search(r"body|(?:^|_)skin", prim["material"], re.IGNORECASE) is not None
            if is_main_skin and best_inc > 0.08:
                calm = [s for s in scored if s[2] < best_inc * 0.5]
                if calm:
                    best_path = max(calm, key=lambda item: item[1])[3]
            out[prim["material"]] = _texture_key(best_path)
    return out


def _is_shiny_like(path: Path) -> bool:
    stem = path.stem.casefold()
    return "rare" in stem or "shiny" in stem


def _dedupe_shiny_variants(paths: list[Path]) -> list[Path]:
    """Keep one sheet per part: the normal variant when both normal and _rare exist.

    Callers building a shiny GLB pass a pre-merged set where the shiny sheet is
    the only variant of its part — that one is kept as-is.
    """
    by_part: dict[str, Path] = {}
    for path in paths:
        key = re.sub(r"_(rare|shiny)(?=_|$)", "", path.stem.casefold())
        current = by_part.get(key)
        if current is None or (_is_shiny_like(current) and not _is_shiny_like(path)):
            by_part[key] = path
    return list(by_part.values())


def _texture_feature_mask(path: Path):
    """Boolean mask of texels that differ strongly from the dominant flat color."""
    import numpy as np
    from PIL import Image

    img = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)
    small = img[::8, ::8].reshape(-1, 3)
    vals, counts = np.unique(small // 24 * 24, axis=0, return_counts=True)
    dominant = vals[counts.argmax()]
    return np.abs(img - dominant).sum(axis=2) > 90.0


def _uv_feature_hit(mask, uv) -> float:
    import numpy as np

    height, width = mask.shape
    u = (np.mod(uv[:, 0], 1.0) * (width - 1)).astype(int)
    v = (np.mod(uv[:, 1], 1.0) * (height - 1)).astype(int)
    return float(mask[v, u].mean())


_MASK_GRID = 128


def _painted_mask(path: Path):
    """Low-res boolean mask of a sheet's painted (non-background) texels."""
    import numpy as np
    from PIL import Image

    img = np.asarray(
        Image.open(path).convert("RGB").resize((_MASK_GRID, _MASK_GRID)), dtype=np.float64
    )
    small = img[::4, ::4].reshape(-1, 3)
    vals, counts = np.unique(small // 24 * 24, axis=0, return_counts=True)
    dominant = vals[counts.argmax()]
    return np.abs(img - dominant).sum(axis=2) > 90.0


def _uv_coverage_mask(uv, faces):
    """Rasterize a primitive's UV triangles into a low-res boolean coverage mask."""
    import numpy as np
    from PIL import Image, ImageDraw

    img = Image.new("L", (_MASK_GRID, _MASK_GRID), 0)
    draw = ImageDraw.Draw(img)
    for face in faces:
        pts = [
            ((uv[j, 0] % 1.0) * (_MASK_GRID - 1), (uv[j, 1] % 1.0) * (_MASK_GRID - 1))
            for j in face
        ]
        draw.polygon(pts, fill=255)
    return np.asarray(img) > 0


def _mask_overlap(cover, painted) -> float:
    """Fraction of the sheet's painted area that the UV islands cover."""
    denom = int(painted.sum())
    if denom <= 0:
        return 0.0
    return float((cover & painted).sum() / denom)


def _mask_iou(cover, painted) -> float:
    """Intersection-over-union between UV island coverage and the painted art."""
    union = int((cover | painted).sum())
    if union <= 0:
        return 0.0
    return float((cover & painted).sum() / union)


def _texture_pixels(path: Path):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64)


def _triangle_incoherence(pixels, uv, faces, samples: int = 8) -> float:
    """Mean fraction of in-triangle samples that disagree with the triangle's modal color.

    Fine detail (wing veins) stays coherent under this measure, while UV islands
    landing on unrelated art straddle color boundaries and score high.
    """
    import numpy as np

    if len(faces) == 0:
        return 0.0
    height, width = pixels.shape[:2]
    bary = np.random.default_rng(0).dirichlet((1.0, 1.0, 1.0), size=samples)
    total = 0.0
    for face in faces:
        s = bary @ uv[face]
        u = (np.mod(s[:, 0], 1.0) * (width - 1)).astype(int)
        v = (np.mod(s[:, 1], 1.0) * (height - 1)).astype(int)
        quantized = (pixels[v, u] // 32).astype(int)
        keys = quantized[:, 0] * 10000 + quantized[:, 1] * 100 + quantized[:, 2]
        _, counts = np.unique(keys, return_counts=True)
        total += 1.0 - counts.max() / samples
    return total / len(faces)


def _bbox_diag(points) -> float:
    import numpy as np

    arr = np.asarray(points, dtype=np.float64)
    if arr.size == 0:
        return 0.0
    return float(np.linalg.norm(arr.max(axis=0) - arr.min(axis=0)))


def _read_glb_primitives(glb_path: Path) -> list[dict]:
    """Minimal GLB reader: per-primitive material name, UVs, and position bounds."""
    import json as _json
    import struct as _struct

    import numpy as np

    data = Path(glb_path).read_bytes()
    if data[:4] != b"glTF":
        raise ValueError("not a GLB file")
    json_len = _struct.unpack_from("<I", data, 12)[0]
    doc = _json.loads(data[20:20 + json_len])
    bin_start = 20 + json_len + 8

    def read_accessor(index: int):
        accessor = doc["accessors"][index]
        view = doc["bufferViews"][accessor["bufferView"]]
        offset = bin_start + view.get("byteOffset", 0) + accessor.get("byteOffset", 0)
        ncomp = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}[accessor["type"]]
        dtype = {5126: np.float32, 5123: np.uint16, 5125: np.uint32, 5121: np.uint8}[accessor["componentType"]]
        return np.frombuffer(
            data, dtype=dtype, count=accessor["count"] * ncomp, offset=offset
        ).reshape(accessor["count"], ncomp).astype(np.float64)

    materials = doc.get("materials") or []
    prims: list[dict] = []
    for mesh in doc.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            attrs = prim.get("attributes") or {}
            if "POSITION" not in attrs or "TEXCOORD_0" not in attrs or "indices" not in prim:
                continue
            mat_index = prim.get("material")
            name = ""
            if isinstance(mat_index, int) and mat_index < len(materials):
                name = str(materials[mat_index].get("name") or "")
            if not name:
                name = str(mesh.get("name") or "")
            pos = read_accessor(attrs["POSITION"])
            faces = read_accessor(prim["indices"]).astype(int).reshape(-1, 3)
            prims.append(
                {
                    "material": name,
                    "uv": read_accessor(attrs["TEXCOORD_0"]),
                    "faces": faces,
                    "pos_bounds": [pos.min(axis=0).tolist(), pos.max(axis=0).tolist()],
                }
            )
    return prims


def col_paths_from(texture_paths: list[Path]) -> list[Path]:
    col = [path for path in texture_paths if _texture_kind(path.stem) == "col"]
    return col or list(texture_paths)


def texture_sheet_entries(texture_paths: list[Path]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for path in sorted(texture_paths, key=lambda item: item.name.casefold()):
        stem = path.stem
        kind = _texture_kind(stem)
        label = stem if not kind else f"{stem} ({kind})"
        entries.append(
            {
                "key": texture_key_for_path(path),
                "name": stem,
                "label": label,
                "path": str(path.resolve()),
            }
        )
    return entries


def _texture_key(path: Path) -> str:
    return texture_key_for_path(path)


def _texture_kind(stem: str) -> str:
    match = _TEXTURE_SUFFIX_RE.search(stem.casefold())
    return match.group(1).casefold() if match else ""


def _material_binding_keys(mesh_name: str) -> list[str]:
    """All lookup keys for a HOME mesh/material label (full name, short BodySkin_N, …)."""
    stem = Path(mesh_name).stem.strip()
    if not stem:
        return []
    keys: list[str] = [stem]
    short = _short_mesh_label(stem)
    if short and short.casefold() != stem.casefold():
        keys.append(short)
    group_suffix = _home_group_suffix(stem)
    part = _mesh_part_token(stem)
    if part and not part.isdigit() and group_suffix is None:
        if part.casefold() not in {stem.casefold(), short.casefold() if short else ""}:
            keys.append(part)
    prefix = _species_prefix(stem)
    if prefix and part and not part.isdigit() and group_suffix is None:
        keys.append(f"{prefix}_{part}")
    out: list[str] = []
    seen: set[str] = set()
    for key in keys:
        norm = key.strip()
        if norm and norm.casefold() not in seen:
            seen.add(norm.casefold())
            out.append(norm)
    return out


def _short_mesh_label(stem: str) -> str:
    """Drop pm####_##_##_ prefix so GLB materials match bindings (BodySkin_3)."""
    match = re.match(r"pm\d{4}_\d{2}_\d{2}_(.+)$", stem, re.IGNORECASE)
    if match:
        return match.group(1)
    return stem


def _mesh_lookup_keys(mesh_name: str) -> list[str]:
    return _material_binding_keys(mesh_name)


def _mesh_part_token(stem: str) -> str:
    base = re.sub(r"_\d+$", "", stem, flags=re.IGNORECASE)
    low = base.casefold()
    for suffix in _MESH_PART_SUFFIXES:
        if low.endswith(suffix) and len(low) > len(suffix):
            token = base[: -len(suffix)]
            if token.endswith("_"):
                token = token[:-1]
            return token.split("_")[-1] if "_" in token else token
    token = base.split("_")[-1] if "_" in base else base
    if token.isdigit():
        return ""
    return token


def _species_prefix(stem: str) -> str:
    match = re.match(r"(pm\d{4}_\d{2}_\d{2})", stem, re.IGNORECASE)
    return match.group(1) if match else ""


def _home_group_suffix(mesh_key: str) -> str | None:
    stem = Path(mesh_key).stem
    if _SPECIES_ID_RE.fullmatch(stem):
        return None
    match = re.search(r"_(\d+)$", stem, re.IGNORECASE)
    if not match:
        return None
    suffix = match.group(1)
    stem_cf = stem.casefold()
    if "skin" in stem_cf or "body" in stem_cf or "eye" in stem_cf:
        return suffix
    if len(suffix) == 1:
        return suffix
    return None


def _col_texture_for_kind(texture_paths: list[Path], kind: str) -> Path | None:
    kind_cf = kind.casefold()
    matches = [
        path
        for path in texture_paths
        if _texture_kind(path.stem) == "col" and kind_cf in path.stem.casefold()
    ]
    if not matches:
        return None
    matches.sort(key=lambda path: (-path.stat().st_size, path.name.casefold()))
    return matches[0]


def _best_texture_for_mesh(mesh_key: str, texture_paths: list[Path]) -> Path | None:
    mesh_cf = mesh_key.casefold()
    prefix = _species_prefix(mesh_key).casefold()
    group_suffix = _home_group_suffix(mesh_key)

    if group_suffix is not None:
        if group_suffix in _HOME_EYE_GROUP_SUFFIXES:
            eye = _col_texture_for_kind(texture_paths, "eye")
            if eye is not None:
                return eye
        body = _col_texture_for_kind(texture_paths, "body")
        if body is not None:
            return body

    if "eye" in mesh_cf:
        eye = _col_texture_for_kind(texture_paths, "eye")
        if eye is not None:
            return eye

    part = _mesh_part_token(mesh_key).casefold()
    if part.isdigit():
        return _col_texture_for_kind(texture_paths, "body")

    ranked: list[tuple[int, int, Path]] = []
    for path in texture_paths:
        stem_cf = path.stem.casefold()
        score = 0
        if prefix and stem_cf.startswith(prefix):
            score += 4
        if part and len(part) > 1 and part in stem_cf:
            score += 6
        if part and stem_cf.endswith(f"{part}_col"):
            score += 10
        elif stem_cf.endswith("_col"):
            score += 3
        elif _texture_kind(path.stem) == "col":
            score += 2
        if score <= 0:
            continue
        ranked.append((score, path.stat().st_size, path))

    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].name.casefold()))
    return ranked[0][2]
