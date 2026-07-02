from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass(slots=True)
class UnityMeshPreviewResult:
    source_bundle: str
    mesh_name: str
    glb_path: str
    mesh_count: int = 0
    warnings: list[str] = field(default_factory=list)
    texture_paths: list[str] = field(default_factory=list)
    texture_by_name: dict[str, str] = field(default_factory=dict)
    material_to_texture: dict[str, str] = field(default_factory=dict)
    texture_bind_order: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _ObjModel:
    positions: list[tuple[float, float, float]]
    faces: list[tuple[int, int, int]]
    uvs: list[tuple[float, float]] = field(default_factory=list)
    normals: list[tuple[float, float, float]] = field(default_factory=list)
    face_groups: list[str] = field(default_factory=list)

    @property
    def has_uvs(self) -> bool:
        return bool(self.uvs) and len(self.uvs) == len(self.positions)

    @property
    def has_normals(self) -> bool:
        return bool(self.normals) and len(self.normals) == len(self.positions)

    @property
    def has_face_groups(self) -> bool:
        return bool(self.face_groups) and len(set(self.face_groups)) > 1


UNITY_MESH_TYPES = {"Mesh"}


def unitypy_available() -> bool:
    try:
        import UnityPy  # noqa: F401
    except Exception:
        return False
    return True


def export_first_mesh_preview_glb(
    bundle_path: str | Path,
    output_dir: str | Path,
    *,
    preferred_names: Iterable[str] | None = None,
) -> UnityMeshPreviewResult:
    try:
        import UnityPy
    except Exception as exc:  # pragma: no cover - runtime guard
        raise RuntimeError(
            "UnityPy is required for phase-2 mobile model previews. Install it into the RAE venv with: "
            "python -m pip install UnityPy"
        ) from exc

    bundle = Path(bundle_path).expanduser().resolve()
    if not bundle.exists():
        raise FileNotFoundError(bundle)

    preferred = [p.casefold() for p in (preferred_names or []) if p]
    env = UnityPy.load(str(bundle))
    mesh_candidates: list[tuple[str, str]] = []
    warnings: list[str] = []

    for obj in env.objects:
        type_name = getattr(getattr(obj, "type", None), "name", "") or ""
        if type_name not in UNITY_MESH_TYPES:
            continue
        try:
            data = obj.read()
        except Exception as exc:
            warnings.append(f"mesh read failed for pathId {getattr(obj, 'path_id', '?')}: {exc}")
            continue
        mesh_name = str(getattr(data, "m_Name", "") or getattr(obj, "name", "") or f"mesh_{getattr(obj, 'path_id', 0)}")
        try:
            exported = data.export()
        except Exception as exc:
            warnings.append(f"mesh export failed for {mesh_name}: {exc}")
            continue
        if isinstance(exported, bytes):
            try:
                obj_text = exported.decode("utf-8", "replace")
            except Exception:
                obj_text = exported.decode("latin-1", "replace")
        else:
            obj_text = str(exported)
        if "\nv " not in "\n" + obj_text:
            warnings.append(f"mesh export for {mesh_name} did not look like OBJ text")
            continue
        mesh_candidates.append((mesh_name, obj_text))

    if not mesh_candidates:
        detail = "; ".join(warnings[:3]) if warnings else "no Mesh objects were exported"
        raise RuntimeError(f"No previewable Unity meshes found in {bundle.name}: {detail}")

    def rank(name: str) -> tuple[int, int, str]:
        low = name.casefold()
        best = 9999
        for idx, pref in enumerate(preferred):
            if pref and pref in low:
                best = idx
                break
        return (best, len(name), low)

    mesh_name, obj_text = sorted(mesh_candidates, key=lambda item: rank(item[0]))[0]
    model = _parse_obj(obj_text)
    if not model.positions or not model.faces:
        raise RuntimeError(f"Mesh {mesh_name} exported but did not contain usable geometry")

    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    safe = _safe_name(f"{bundle.stem}__{mesh_name}")
    glb_path = output_root / f"{safe}.glb"
    _write_glb(model, glb_path, mesh_name=mesh_name)
    return UnityMeshPreviewResult(
        source_bundle=str(bundle),
        mesh_name=mesh_name,
        glb_path=str(glb_path),
        mesh_count=len(mesh_candidates),
        warnings=warnings,
    )


def _parse_obj(obj_text: str) -> _ObjModel:
    raw_positions: list[tuple[float, float, float]] = []
    raw_uvs: list[tuple[float, float]] = []
    raw_normals: list[tuple[float, float, float]] = []
    out_positions: list[tuple[float, float, float]] = []
    out_uvs: list[tuple[float, float]] = []
    out_normals: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_groups: list[str] = []
    vert_map: dict[tuple[int, int | None, int | None], int] = {}
    has_raw_uvs = False
    has_raw_normals = False
    current_group = ""

    def _parse_index(token: str, size: int) -> int | None:
        if not token:
            return None
        try:
            idx = int(token)
        except Exception:
            return None
        if idx < 0:
            idx = size + idx
        else:
            idx -= 1
        if idx < 0 or idx >= size:
            return None
        return idx

    def _corner_index(face_token: str) -> int | None:
        parts = face_token.split("/")
        if not parts or not parts[0]:
            return None
        vi = _parse_index(parts[0], len(raw_positions))
        if vi is None:
            return None
        vti = _parse_index(parts[1], len(raw_uvs)) if len(parts) > 1 else None
        vni = _parse_index(parts[2], len(raw_normals)) if len(parts) > 2 else None
        key = (vi, vti, vni)
        cached = vert_map.get(key)
        if cached is not None:
            return cached
        out_positions.append(raw_positions[vi])
        if vti is not None:
            out_uvs.append(raw_uvs[vti])
        else:
            out_uvs.append((0.0, 0.0))
        if vni is not None:
            out_normals.append(raw_normals[vni])
        else:
            out_normals.append((0.0, 0.0, 0.0))
        idx = len(out_positions) - 1
        vert_map[key] = idx
        return idx

    for raw in obj_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("v "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    raw_positions.append((float(parts[1]), float(parts[2]), float(parts[3])))
                except Exception:
                    continue
        elif line.startswith("vt "):
            parts = line.split()
            if len(parts) >= 3:
                try:
                    raw_uvs.append((float(parts[1]), float(parts[2])))
                    has_raw_uvs = True
                except Exception:
                    continue
        elif line.startswith("vn "):
            parts = line.split()
            if len(parts) >= 4:
                try:
                    raw_normals.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    has_raw_normals = True
                except Exception:
                    continue
        elif line.startswith("g ") or line.startswith("usemtl "):
            current_group = line.split(maxsplit=1)[1].strip() if len(line.split()) > 1 else ""
        elif line.startswith("f "):
            corners: list[int] = []
            for part in line.split()[1:]:
                idx = _corner_index(part)
                if idx is not None:
                    corners.append(idx)
            if len(corners) >= 3:
                root = corners[0]
                for i in range(1, len(corners) - 1):
                    faces.append((root, corners[i], corners[i + 1]))
                    face_groups.append(current_group)

    return _ObjModel(
        positions=out_positions,
        faces=faces,
        uvs=out_uvs if has_raw_uvs else [],
        normals=out_normals if has_raw_normals else [],
        face_groups=face_groups,
    )


def _merge_obj_models(named_models: list[tuple[str, _ObjModel]]) -> _ObjModel:
    """Concatenate parsed OBJ meshes into one model, preserving per-face groups.

    Faces with empty/duplicate group names are namespaced by their mesh name so
    material splitting keeps parts from different source meshes separate.
    """
    positions: list[tuple[float, float, float]] = []
    uvs: list[tuple[float, float]] = []
    normals: list[tuple[float, float, float]] = []
    faces: list[tuple[int, int, int]] = []
    face_groups: list[str] = []
    any_uvs = any(m.has_uvs for _, m in named_models)
    any_normals = any(m.has_normals for _, m in named_models)
    seen_groups: set[str] = set()
    for mesh_name, model in named_models:
        offset = len(positions)
        positions.extend(model.positions)
        if any_uvs:
            uvs.extend(model.uvs if model.has_uvs else [(0.0, 0.0)] * len(model.positions))
        if any_normals:
            normals.extend(model.normals if model.has_normals else [(0.0, 0.0, 0.0)] * len(model.positions))
        groups = model.face_groups if model.face_groups else [""] * len(model.faces)
        local_groups = {g for g in groups if g}
        clash = bool(local_groups & seen_groups)
        seen_groups.update(local_groups)
        for face, group in zip(model.faces, groups):
            faces.append((face[0] + offset, face[1] + offset, face[2] + offset))
            label = group or mesh_name
            if clash and group:
                label = f"{mesh_name}__{group}"
            face_groups.append(label)
    return _ObjModel(
        positions=positions,
        faces=faces,
        uvs=uvs if any_uvs else [],
        normals=normals if any_normals else [],
        face_groups=face_groups,
    )


def _split_obj_by_groups(model: _ObjModel) -> list[tuple[str, _ObjModel]]:
    """Split an OBJ model into one sub-mesh per `g` group (AssetStudio material slots)."""
    if not model.faces:
        return []
    if not model.has_face_groups:
        label = model.face_groups[0] if model.face_groups else "mesh"
        return [(label, model)]

    grouped_faces: dict[str, list[tuple[int, int, int]]] = {}
    for face, group in zip(model.faces, model.face_groups):
        grouped_faces.setdefault(group or "mesh", []).append(face)

    parts: list[tuple[str, _ObjModel]] = []
    for group_name, group_faces in grouped_faces.items():
        if not group_faces:
            continue
        used = sorted({index for face in group_faces for index in face})
        remap = {old: new for new, old in enumerate(used)}
        positions = [model.positions[index] for index in used]
        uvs = [model.uvs[index] for index in used] if model.has_uvs else []
        normals = [model.normals[index] for index in used] if model.has_normals else []
        faces = [(remap[a], remap[b], remap[c]) for a, b, c in group_faces]
        parts.append(
            (
                group_name,
                _ObjModel(positions=positions, faces=faces, uvs=uvs, normals=normals),
            )
        )
    return parts


def _obj_uv_to_gltf(u: float, v: float) -> tuple[float, float]:
    """Convert AssetStudio OBJ UVs to glTF (V=0 at top, tile-safe for Unity UVs > 1)."""
    frac = v - math.floor(v)
    return u, 1.0 - frac


def _write_glb(model: _ObjModel, out_path: Path, *, mesh_name: str = "PreviewMesh") -> None:
    if not model.positions or not model.faces:
        raise ValueError("Cannot write GLB from empty geometry")

    parts = _split_obj_by_groups(model)
    if len(parts) > 1:
        _write_glb_grouped(parts, out_path, mesh_name=mesh_name)
        return

    if len(parts) == 1 and parts[0][1] is not model:
        model = parts[0][1]
        if parts[0][0]:
            mesh_name = parts[0][0]

    try:
        import numpy as np
        import trimesh

        vertices = np.asarray(model.positions, dtype=np.float64)
        faces = np.asarray(model.faces, dtype=np.int64)
        vertex_normals = None
        if model.has_normals:
            vertex_normals = np.asarray(model.normals, dtype=np.float64)
        mesh = trimesh.Trimesh(
            vertices=vertices,
            faces=faces,
            vertex_normals=vertex_normals,
            process=False,
        )
        mesh.metadata["name"] = mesh_name
        if model.has_uvs:
            uvs = np.asarray(model.uvs, dtype=np.float64)
            uvs = uvs.copy()
            uvs[:, 0], uvs[:, 1] = zip(*(_obj_uv_to_gltf(u, v) for u, v in uvs))
            mesh.visual = trimesh.visual.TextureVisuals(uv=uvs)
        scene = trimesh.Scene(mesh)
        scene.export(out_path, file_type="glb")
        _strip_trimesh_placeholder_textures(out_path, mesh_name)
        return
    except Exception:
        pass

    _write_glb_manual(model, out_path, mesh_name=mesh_name)


def _strip_trimesh_placeholder_textures(out_path: Path, mesh_name: str) -> None:
    """Remove trimesh's embedded 2x2 placeholder texture so preview patching can bind real PNGs."""
    try:
        import copy

        from ...glb_policy.glb_io import GlbData, read_glb

        glb = read_glb(out_path)
        images = glb.json.get("images") or []
        if not any(isinstance(image, dict) and image.get("bufferView") is not None for image in images):
            return
        gltf = copy.deepcopy(glb.json)
        gltf["images"] = []
        gltf["textures"] = []
        materials = list(gltf.get("materials") or [])
        for material in materials:
            if not isinstance(material, dict):
                continue
            if not str(material.get("name") or "").strip():
                material["name"] = mesh_name
            pbr = material.setdefault("pbrMetallicRoughness", {})
            if isinstance(pbr, dict):
                pbr.pop("baseColorTexture", None)
                pbr["baseColorFactor"] = [1.0, 1.0, 1.0, 1.0]
            material["doubleSided"] = True
        gltf["materials"] = materials
        GlbData(json=gltf, bin_chunk=glb.bin_chunk).write(out_path)
    except Exception:
        return


def _write_glb_grouped(parts: list[tuple[str, _ObjModel]], out_path: Path, *, mesh_name: str) -> None:
    """Write one glTF mesh with a primitive and named material per OBJ group."""
    primitives_data = [(name, part) for name, part in parts if part.positions and part.faces]
    if not primitives_data:
        raise ValueError("Cannot write GLB from empty geometry")
    if len(primitives_data) == 1:
        _write_glb(primitives_data[0][1], out_path, mesh_name=primitives_data[0][0] or mesh_name)
        return

    bin_blob = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    primitives: list[dict] = []
    materials: list[dict] = []

    def pad4(blob: bytearray) -> bytearray:
        while len(blob) % 4:
            blob.append(0)
        return blob

    def append_blob(blob: bytearray) -> int:
        nonlocal bin_blob
        pad4(bin_blob)
        offset = len(bin_blob)
        bin_blob.extend(blob)
        pad4(bin_blob)
        return offset

    for part_idx, (part_name, part) in enumerate(primitives_data):
        pos_blob = bytearray()
        min_v = [math.inf, math.inf, math.inf]
        max_v = [-math.inf, -math.inf, -math.inf]
        for x, y, z in part.positions:
            pos_blob += struct.pack("<3f", x, y, z)
            min_v[0] = min(min_v[0], x)
            min_v[1] = min(min_v[1], y)
            min_v[2] = min(min_v[2], z)
            max_v[0] = max(max_v[0], x)
            max_v[1] = max(max_v[1], y)
            max_v[2] = max(max_v[2], z)
        pos_offset = append_blob(pos_blob)
        pos_view = len(buffer_views)
        buffer_views.append(
            {"buffer": 0, "byteOffset": pos_offset, "byteLength": len(pos_blob), "target": 34962}
        )
        pos_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": pos_view,
                "byteOffset": 0,
                "componentType": 5126,
                "count": len(part.positions),
                "type": "VEC3",
                "min": [0.0 if math.isinf(v) else float(v) for v in min_v],
                "max": [0.0 if math.isinf(v) else float(v) for v in max_v],
            }
        )
        primitive_attrs: dict[str, int] = {"POSITION": pos_accessor}

        if part.has_normals:
            nrm_blob = bytearray()
            for nx, ny, nz in part.normals:
                length = math.sqrt(nx * nx + ny * ny + nz * nz)
                if length > 1e-8:
                    nx, ny, nz = nx / length, ny / length, nz / length
                nrm_blob += struct.pack("<3f", nx, ny, nz)
            nrm_offset = append_blob(nrm_blob)
            nrm_view = len(buffer_views)
            buffer_views.append(
                {"buffer": 0, "byteOffset": nrm_offset, "byteLength": len(nrm_blob), "target": 34962}
            )
            nrm_accessor = len(accessors)
            accessors.append(
                {
                    "bufferView": nrm_view,
                    "byteOffset": 0,
                    "componentType": 5126,
                    "count": len(part.normals),
                    "type": "VEC3",
                }
            )
            primitive_attrs["NORMAL"] = nrm_accessor

        if part.has_uvs:
            uv_blob = bytearray()
            min_uv = [math.inf, math.inf]
            max_uv = [-math.inf, -math.inf]
            for u, v in part.uvs:
                gu, gv = _obj_uv_to_gltf(u, v)
                uv_blob += struct.pack("<2f", gu, gv)
                min_uv[0] = min(min_uv[0], gu)
                min_uv[1] = min(min_uv[1], gv)
                max_uv[0] = max(max_uv[0], gu)
                max_uv[1] = max(max_uv[1], gv)
            uv_offset = append_blob(uv_blob)
            uv_view = len(buffer_views)
            buffer_views.append(
                {"buffer": 0, "byteOffset": uv_offset, "byteLength": len(uv_blob), "target": 34962}
            )
            uv_accessor = len(accessors)
            accessors.append(
                {
                    "bufferView": uv_view,
                    "byteOffset": 0,
                    "componentType": 5126,
                    "count": len(part.uvs),
                    "type": "VEC2",
                    "min": [0.0 if math.isinf(v) else float(v) for v in min_uv],
                    "max": [0.0 if math.isinf(v) else float(v) for v in max_uv],
                }
            )
            primitive_attrs["TEXCOORD_0"] = uv_accessor

        max_index = max(max(face) for face in part.faces)
        use_u16 = max_index <= 65535
        idx_blob = bytearray()
        if use_u16:
            for a, b, c in part.faces:
                idx_blob += struct.pack("<3H", a, b, c)
            component_type = 5123
        else:
            for a, b, c in part.faces:
                idx_blob += struct.pack("<3I", a, b, c)
            component_type = 5125
        idx_offset = append_blob(idx_blob)
        idx_view = len(buffer_views)
        buffer_views.append(
            {"buffer": 0, "byteOffset": idx_offset, "byteLength": len(idx_blob), "target": 34963}
        )
        idx_accessor = len(accessors)
        accessors.append(
            {
                "bufferView": idx_view,
                "byteOffset": 0,
                "componentType": component_type,
                "count": len(part.faces) * 3,
                "type": "SCALAR",
            }
        )

        material_index = len(materials)
        materials.append(
            {
                "name": part_name or f"{mesh_name}_{part_idx}",
                "pbrMetallicRoughness": {
                    "baseColorFactor": [1.0, 1.0, 1.0, 1.0],
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.9,
                },
                "doubleSided": True,
            }
        )
        primitives.append(
            {
                "attributes": primitive_attrs,
                "indices": idx_accessor,
                "mode": 4,
                "material": material_index,
            }
        )

    gltf = {
        "asset": {"version": "2.0", "generator": "RAE mobile mesh preview"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "meshes": [{"name": mesh_name, "primitives": primitives}],
        "nodes": [{"mesh": 0, "name": mesh_name}],
        "materials": materials,
        "buffers": [{"byteLength": len(bin_blob)}],
        "bufferViews": buffer_views,
        "accessors": accessors,
    }
    json_blob = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    json_pad = (4 - (len(json_blob) % 4)) % 4
    json_blob += b" " * json_pad
    total_length = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    with out_path.open("wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total_length))
        fh.write(struct.pack("<I4s", len(json_blob), b"JSON"))
        fh.write(json_blob)
        fh.write(struct.pack("<I4s", len(bin_blob), b"BIN\x00"))
        fh.write(bin_blob)


def _write_glb_manual(model: _ObjModel, out_path: Path, *, mesh_name: str = "PreviewMesh") -> None:
    pos_blob = bytearray()
    uv_blob = bytearray()
    nrm_blob = bytearray()
    min_v = [math.inf, math.inf, math.inf]
    max_v = [-math.inf, -math.inf, -math.inf]
    min_uv = [math.inf, math.inf]
    max_uv = [-math.inf, -math.inf]
    for x, y, z in model.positions:
        pos_blob += struct.pack('<3f', x, y, z)
        min_v[0] = min(min_v[0], x)
        min_v[1] = min(min_v[1], y)
        min_v[2] = min(min_v[2], z)
        max_v[0] = max(max_v[0], x)
        max_v[1] = max(max_v[1], y)
        max_v[2] = max(max_v[2], z)
    if model.has_uvs:
        for u, v in model.uvs:
            gu, gv = _obj_uv_to_gltf(u, v)
            uv_blob += struct.pack('<2f', gu, gv)
            min_uv[0] = min(min_uv[0], gu)
            min_uv[1] = min(min_uv[1], gv)
            max_uv[0] = max(max_uv[0], gu)
            max_uv[1] = max(max_uv[1], gv)
    if model.has_normals:
        for nx, ny, nz in model.normals:
            length = math.sqrt(nx * nx + ny * ny + nz * nz)
            if length > 1e-8:
                nx, ny, nz = nx / length, ny / length, nz / length
            nrm_blob += struct.pack('<3f', nx, ny, nz)

    max_index = max(max(face) for face in model.faces)
    use_u16 = max_index <= 65535
    idx_blob = bytearray()
    if use_u16:
        for a, b, c in model.faces:
            idx_blob += struct.pack('<3H', a, b, c)
        component_type = 5123
    else:
        for a, b, c in model.faces:
            idx_blob += struct.pack('<3I', a, b, c)
        component_type = 5125

    def pad4(blob: bytearray) -> bytearray:
        while len(blob) % 4:
            blob += b'\x00'
        return blob

    pos_blob = pad4(pos_blob)
    pos_offset = 0
    uv_offset = len(pos_blob)
    if model.has_uvs:
        uv_blob = pad4(uv_blob)
    nrm_offset = uv_offset + (len(uv_blob) if model.has_uvs else 0)
    if model.has_normals:
        nrm_blob = pad4(nrm_blob)
    idx_offset = nrm_offset + (len(nrm_blob) if model.has_normals else 0)
    idx_blob = pad4(idx_blob)
    bin_blob = bytes(
        pos_blob
        + (uv_blob if model.has_uvs else b'')
        + (nrm_blob if model.has_normals else b'')
        + idx_blob
    )

    primitive_attrs: dict[str, int] = {'POSITION': 0}
    accessors: list[dict] = [
        {
            'bufferView': 0,
            'byteOffset': 0,
            'componentType': 5126,
            'count': len(model.positions),
            'type': 'VEC3',
            'min': [0.0 if math.isinf(v) else float(v) for v in min_v],
            'max': [0.0 if math.isinf(v) else float(v) for v in max_v],
        },
    ]
    buffer_views: list[dict] = [
        {'buffer': 0, 'byteOffset': pos_offset, 'byteLength': len(pos_blob), 'target': 34962},
    ]
    next_slot = 1
    if model.has_uvs:
        primitive_attrs['TEXCOORD_0'] = next_slot
        buffer_views.append(
            {'buffer': 0, 'byteOffset': uv_offset, 'byteLength': len(uv_blob), 'target': 34962}
        )
        accessors.append(
            {
                'bufferView': next_slot,
                'byteOffset': 0,
                'componentType': 5126,
                'count': len(model.uvs),
                'type': 'VEC2',
                'min': [0.0 if math.isinf(v) else float(v) for v in min_uv],
                'max': [0.0 if math.isinf(v) else float(v) for v in max_uv],
            }
        )
        next_slot += 1
    if model.has_normals:
        primitive_attrs['NORMAL'] = next_slot
        buffer_views.append(
            {'buffer': 0, 'byteOffset': nrm_offset, 'byteLength': len(nrm_blob), 'target': 34962}
        )
        accessors.append(
            {
                'bufferView': next_slot,
                'byteOffset': 0,
                'componentType': 5126,
                'count': len(model.normals),
                'type': 'VEC3',
            }
        )
        next_slot += 1
    idx_view = next_slot
    idx_accessor = next_slot

    buffer_views.append(
        {'buffer': 0, 'byteOffset': idx_offset, 'byteLength': len(idx_blob), 'target': 34963}
    )
    accessors.append(
        {
            'bufferView': idx_view,
            'byteOffset': 0,
            'componentType': component_type,
            'count': len(model.faces) * 3,
            'type': 'SCALAR',
        }
    )

    gltf = {
        'asset': {'version': '2.0', 'generator': 'RAE mobile phase2 mesh preview'},
        'scene': 0,
        'scenes': [{'nodes': [0]}],
        'meshes': [{'name': mesh_name, 'primitives': [{'attributes': primitive_attrs, 'indices': idx_accessor, 'mode': 4}]}],
        'nodes': [{'mesh': 0, 'name': mesh_name}],
        'buffers': [{'byteLength': len(bin_blob)}],
        'bufferViews': buffer_views,
        'accessors': accessors,
    }
    json_blob = json.dumps(gltf, separators=(',', ':')).encode('utf-8')
    json_pad = (4 - (len(json_blob) % 4)) % 4
    json_blob += b' ' * json_pad
    total_length = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    with out_path.open('wb') as fh:
        fh.write(struct.pack('<4sII', b'glTF', 2, total_length))
        fh.write(struct.pack('<I4s', len(json_blob), b'JSON'))
        fh.write(json_blob)
        fh.write(struct.pack('<I4s', len(bin_blob), b'BIN\x00'))
        fh.write(bin_blob)


def _safe_name(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum() or ch in {'_', '-', '.'}:
            keep.append(ch)
        else:
            keep.append('_')
    return ''.join(keep).strip('._') or 'mobile_preview'
