"""Self-contained GLB writer for 3DS GFModel meshes with embedded PNG textures."""
from __future__ import annotations

import json
import math
import struct
from pathlib import Path

from .gf import GfBone, GfModel, GfTexture
from .motion import GfMotion, bake_motion


def _quat_from_euler_xyz(x: float, y: float, z: float) -> tuple[float, float, float, float]:
    """Bone rotation quaternion q = qz * qy * qx, as glTF (x, y, z, w)."""
    cx, sx = math.cos(x * 0.5), math.sin(x * 0.5)
    cy, sy = math.cos(y * 0.5), math.sin(y * 0.5)
    cz, sz = math.cos(z * 0.5), math.sin(z * 0.5)
    return (
        sx * cy * cz - cx * sy * sz,
        cx * sy * cz + sx * cy * sz,
        cx * cy * sz - sx * sy * cz,
        cx * cy * cz + sx * sy * sz,
    )


def _local_matrix(bone: GfBone) -> list[list[float]]:
    """Row-major 4x4 local transform T * R * S (R = Rz*Ry*Rx)."""
    x, y, z, w = _quat_from_euler_xyz(*bone.rotation)
    sx, sy, sz = bone.scale
    tx, ty, tz = bone.translation
    r = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    scale = (sx, sy, sz)
    return [
        [r[0][0] * scale[0], r[0][1] * scale[1], r[0][2] * scale[2], tx],
        [r[1][0] * scale[0], r[1][1] * scale[1], r[1][2] * scale[2], ty],
        [r[2][0] * scale[0], r[2][1] * scale[1], r[2][2] * scale[2], tz],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
        for i in range(4)
    ]


def _affine_inverse(m: list[list[float]]) -> list[list[float]]:
    """Inverse of a row-major affine 4x4 (rotation * scale + translation)."""
    a = [[m[i][j] for j in range(3)] for i in range(3)]
    det = (
        a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
        - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
        + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0])
    )
    if abs(det) < 1e-12:
        det = 1e-12 if det >= 0 else -1e-12
    inv = [
        [
            (a[1][1] * a[2][2] - a[1][2] * a[2][1]) / det,
            (a[0][2] * a[2][1] - a[0][1] * a[2][2]) / det,
            (a[0][1] * a[1][2] - a[0][2] * a[1][1]) / det,
        ],
        [
            (a[1][2] * a[2][0] - a[1][0] * a[2][2]) / det,
            (a[0][0] * a[2][2] - a[0][2] * a[2][0]) / det,
            (a[0][2] * a[1][0] - a[0][0] * a[1][2]) / det,
        ],
        [
            (a[1][0] * a[2][1] - a[1][1] * a[2][0]) / det,
            (a[0][1] * a[2][0] - a[0][0] * a[2][1]) / det,
            (a[0][0] * a[1][1] - a[0][1] * a[1][0]) / det,
        ],
    ]
    t = [m[0][3], m[1][3], m[2][3]]
    it = [-sum(inv[i][k] * t[k] for k in range(3)) for i in range(3)]
    return [
        [inv[0][0], inv[0][1], inv[0][2], it[0]],
        [inv[1][0], inv[1][1], inv[1][2], it[1]],
        [inv[2][0], inv[2][1], inv[2][2], it[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _column_major(m: list[list[float]]) -> list[float]:
    return [m[row][col] for col in range(4) for row in range(4)]


def _is_incandescent_material(name: str) -> bool:
    """Game Freak marks emissive overlay layers with an "Inc" suffix
    (BodyANeolant_Inc, EyeInc, ...)."""
    return name.lower().endswith("inc")


def write_model_glb(
    model: GfModel,
    textures: list[GfTexture],
    out_path: str | Path,
    *,
    scene_name: str | None = None,
    animations: list[GfMotion] | None = None,
) -> Path:
    """Write *model* as a GLB with PNG textures embedded in the binary chunk.

    Each material's first texture unit (the albedo map) becomes the glTF
    base-color texture. V coordinates are flipped to the glTF convention.
    """
    out_path = Path(out_path)
    bin_blob = bytearray()
    buffer_views: list[dict] = []
    accessors: list[dict] = []
    images: list[dict] = []
    gltf_textures: list[dict] = []
    materials: list[dict] = []
    meshes: list[dict] = []
    nodes: list[dict] = []

    def add_view(data: bytes, target: int | None = None) -> int:
        while len(bin_blob) % 4:
            bin_blob.append(0)
        view = {"buffer": 0, "byteOffset": len(bin_blob), "byteLength": len(data)}
        if target is not None:
            view["target"] = target
        bin_blob.extend(data)
        buffer_views.append(view)
        return len(buffer_views) - 1

    # -- textures ------------------------------------------------------------
    texture_index_by_name: dict[str, int] = {}
    texture_is_opaque: dict[str, bool] = {}
    samplers: list[dict] = []
    sampler_index_by_wrap: dict[tuple[int, int], int] = {}

    def _gl_wrap(mode: int) -> int:
        # GF wrap: 0 clamp-edge, 1 clamp-border, 2 repeat, 3 mirror
        return {0: 33071, 1: 33071, 2: 10497, 3: 33648}.get(mode, 10497)

    def sampler_for(wrap_u: int, wrap_v: int) -> int:
        key = (wrap_u, wrap_v)
        if key not in sampler_index_by_wrap:
            samplers.append(
                {
                    "magFilter": 9729,
                    "minFilter": 9987,
                    "wrapS": _gl_wrap(wrap_u),
                    "wrapT": _gl_wrap(wrap_v),
                }
            )
            sampler_index_by_wrap[key] = len(samplers) - 1
        return sampler_index_by_wrap[key]

    png_by_name: dict[str, int] = {}
    for tex in textures:
        try:
            rgba = tex.decode_rgba()
        except Exception:
            continue
        alphas = rgba[3::4]
        zero = sum(1 for a in alphas if a == 0)
        from .pica import rgba_to_png

        png = rgba_to_png(rgba, tex.width, tex.height)
        view_index = add_view(png)
        images.append({"bufferView": view_index, "mimeType": "image/png", "name": tex.name})
        png_by_name[tex.name] = len(images) - 1
        texture_is_opaque[tex.name] = zero == 0

    def texture_for(name: str, wrap_u: int, wrap_v: int) -> int:
        key = f"{name}|{wrap_u}|{wrap_v}"
        if key not in texture_index_by_name:
            gltf_textures.append(
                {"sampler": sampler_for(wrap_u, wrap_v), "source": png_by_name[name], "name": name}
            )
            texture_index_by_name[key] = len(gltf_textures) - 1
        return texture_index_by_name[key]

    # -- materials -------------------------------------------------------------
    material_index_by_name: dict[str, int] = {}
    uv_transform_by_material: dict[str, tuple[float, float, float, float]] = {}
    for mat in model.materials:
        entry: dict = {
            "name": mat.name,
            "pbrMetallicRoughness": {
                "metallicFactor": 0.0,
                "roughnessFactor": 0.9,
            },
            "doubleSided": True,
        }
        albedo = next(
            (name for name in mat.texture_names if name in png_by_name),
            None,
        )
        if albedo is not None:
            unit = next((u for u in mat.texture_units if u.name == albedo), None)
            wrap_u, wrap_v = (unit.wrap_u, unit.wrap_v) if unit else (2, 2)
            entry["pbrMetallicRoughness"]["baseColorTexture"] = {
                "index": texture_for(albedo, wrap_u, wrap_v)
            }
            if not texture_is_opaque.get(albedo, True):
                # Overlay maps (iris/pupil) rely on alpha blending.
                entry["alphaMode"] = "BLEND"
            if _is_incandescent_material(mat.name) and mat.specular0 is not None:
                # "*_Inc" overlay layers are grayscale masks tinted by the
                # material's specular0 color in the TEV pipeline (e.g. the red
                # glowing lines on Kyogre). Bake that tint into the base color.
                red, green, blue = (int(c) for c in mat.specular0[:3])
                if (red, green, blue) != (0, 0, 0):
                    entry["pbrMetallicRoughness"]["baseColorFactor"] = [
                        red / 255.0,
                        green / 255.0,
                        blue / 255.0,
                        1.0,
                    ]
            if unit is not None:
                uv_transform_by_material[mat.name] = (
                    unit.scale[0],
                    unit.scale[1],
                    unit.translation[0],
                    unit.translation[1],
                )
        material_index_by_name[mat.name] = len(materials)
        materials.append(entry)

    # -- skeleton --------------------------------------------------------------
    # Bone nodes occupy indices 0..len(bones)-1 so mesh nodes come after them.
    skins: list[dict] = []
    bone_index_by_name: dict[str, int] = {b.name: i for i, b in enumerate(model.bones)}
    skeleton_roots: list[int] = []
    has_skinning = bool(model.bones) and any(
        sub.joints for mesh in model.meshes for sub in mesh.submeshes
    )
    if model.bones:
        children_by_parent: dict[int, list[int]] = {}
        for i, bone in enumerate(model.bones):
            node = {"name": bone.name}
            if bone.translation != (0.0, 0.0, 0.0):
                node["translation"] = list(bone.translation)
            if bone.rotation != (0.0, 0.0, 0.0):
                node["rotation"] = list(_quat_from_euler_xyz(*bone.rotation))
            if bone.scale != (1.0, 1.0, 1.0):
                node["scale"] = list(bone.scale)
            nodes.append(node)
            parent = bone_index_by_name.get(bone.parent, -1) if bone.parent else -1
            if parent >= 0 and parent != i:
                children_by_parent.setdefault(parent, []).append(i)
            else:
                skeleton_roots.append(i)
        for parent, children in children_by_parent.items():
            nodes[parent]["children"] = children

        if has_skinning:
            world: list[list[list[float]]] = [None] * len(model.bones)  # type: ignore[list-item]
            for i, bone in enumerate(model.bones):
                local = _local_matrix(bone)
                parent = bone_index_by_name.get(bone.parent, -1) if bone.parent else -1
                world[i] = _mat_mul(world[parent], local) if 0 <= parent < i else local
            ibm_data = b"".join(
                struct.pack("<16f", *_column_major(_affine_inverse(w))) for w in world
            )
            ibm_view = add_view(ibm_data)
            accessors.append(
                {
                    "bufferView": ibm_view,
                    "componentType": 5126,
                    "count": len(model.bones),
                    "type": "MAT4",
                }
            )
            skins.append(
                {
                    "name": f"{model.name}_skin",
                    "joints": list(range(len(model.bones))),
                    "inverseBindMatrices": len(accessors) - 1,
                    "skeleton": skeleton_roots[0] if skeleton_roots else 0,
                }
            )

    # -- geometry --------------------------------------------------------------
    for mesh in model.meshes:
        primitives = []
        for sub in mesh.submeshes:
            if not sub.positions or not sub.indices:
                continue
            count = len(sub.positions)
            pos_data = b"".join(struct.pack("<3f", *p) for p in sub.positions)
            mins = [min(p[i] for p in sub.positions) for i in range(3)]
            maxs = [max(p[i] for p in sub.positions) for i in range(3)]
            pos_view = add_view(pos_data, target=34962)
            accessors.append(
                {
                    "bufferView": pos_view,
                    "componentType": 5126,
                    "count": count,
                    "type": "VEC3",
                    "min": mins,
                    "max": maxs,
                }
            )
            attributes = {"POSITION": len(accessors) - 1}

            if len(sub.normals) == count:
                normals = []
                for n in sub.normals:
                    length = (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5
                    if length > 1e-6:
                        normals.append((n[0] / length, n[1] / length, n[2] / length))
                    else:
                        normals.append((0.0, 1.0, 0.0))
                nrm_view = add_view(b"".join(struct.pack("<3f", *n) for n in normals), target=34962)
                accessors.append(
                    {"bufferView": nrm_view, "componentType": 5126, "count": count, "type": "VEC3"}
                )
                attributes["NORMAL"] = len(accessors) - 1

            if len(sub.uvs) == count:
                # Bake the material's texture-coordinate transform (frame
                # selection for eye/iris sheets) into the exported UVs.
                sx, sy, tx, ty = uv_transform_by_material.get(sub.material_name, (1.0, 1.0, 0.0, 0.0))
                uv_data = b"".join(
                    struct.pack("<2f", u * sx + tx, 1.0 - (v * sy + ty)) for u, v in sub.uvs
                )
                uv_view = add_view(uv_data, target=34962)
                accessors.append(
                    {"bufferView": uv_view, "componentType": 5126, "count": count, "type": "VEC2"}
                )
                attributes["TEXCOORD_0"] = len(accessors) - 1

            if has_skinning and len(sub.joints) == count and len(sub.weights) == count:
                joint_rows: list[tuple[int, int, int, int]] = []
                weight_rows: list[tuple[float, float, float, float]] = []
                table = sub.bone_table
                bone_count = len(model.bones)
                for (j0, j1, j2, j3), (w0, w1, w2, w3) in zip(sub.joints, sub.weights):
                    raw = (j0, j1, j2, j3)
                    w = [max(0.0, w0), max(0.0, w1), max(0.0, w2), max(0.0, w3)]
                    total = w[0] + w[1] + w[2] + w[3]
                    if total <= 1e-6:
                        w = [1.0, 0.0, 0.0, 0.0]
                    else:
                        w = [v / total for v in w]
                    mapped = []
                    for slot in range(4):
                        idx = raw[slot]
                        if table and idx < len(table):
                            idx = table[idx]
                        if idx >= bone_count or w[slot] == 0.0:
                            idx = idx if idx < bone_count else 0
                        mapped.append(idx)
                    joint_rows.append(tuple(mapped))
                    weight_rows.append(tuple(w))
                joints_view = add_view(
                    b"".join(struct.pack("<4B", *j) for j in joint_rows), target=34962
                )
                accessors.append(
                    {"bufferView": joints_view, "componentType": 5121, "count": count, "type": "VEC4"}
                )
                attributes["JOINTS_0"] = len(accessors) - 1
                weights_view = add_view(
                    b"".join(struct.pack("<4f", *w) for w in weight_rows), target=34962
                )
                accessors.append(
                    {"bufferView": weights_view, "componentType": 5126, "count": count, "type": "VEC4"}
                )
                attributes["WEIGHTS_0"] = len(accessors) - 1

            idx_data = b"".join(struct.pack("<H", i) for i in sub.indices)
            idx_view = add_view(idx_data, target=34963)
            accessors.append(
                {
                    "bufferView": idx_view,
                    "componentType": 5123,
                    "count": len(sub.indices),
                    "type": "SCALAR",
                }
            )
            primitive = {"attributes": attributes, "indices": len(accessors) - 1, "mode": 4}
            mat_index = material_index_by_name.get(sub.material_name)
            if mat_index is not None:
                primitive["material"] = mat_index
            primitives.append(primitive)
        if not primitives:
            continue
        meshes.append({"name": mesh.name, "primitives": primitives})
        mesh_node = {"mesh": len(meshes) - 1, "name": mesh.name}
        if has_skinning and any("JOINTS_0" in p["attributes"] for p in primitives):
            mesh_node["skin"] = 0
        nodes.append(mesh_node)

    # -- animations --------------------------------------------------------------
    gltf_animations: list[dict] = []
    if animations and model.bones:
        rest_pose = {b.name: (b.scale, b.rotation, b.translation) for b in model.bones}
        for motion in animations:
            times, baked = bake_motion(motion, rest_pose)
            if len(times) < 2 or not baked:
                continue
            time_view = add_view(b"".join(struct.pack("<f", t) for t in times))
            accessors.append(
                {
                    "bufferView": time_view,
                    "componentType": 5126,
                    "count": len(times),
                    "type": "SCALAR",
                    "min": [times[0]],
                    "max": [times[-1]],
                }
            )
            time_accessor = len(accessors) - 1
            samplers_a: list[dict] = []
            channels_a: list[dict] = []
            for bone_anim in baked:
                node_index = bone_index_by_name.get(bone_anim.name)
                if node_index is None:
                    continue
                for path, values, fmt in (
                    ("translation", bone_anim.translations, "<3f"),
                    ("rotation", bone_anim.rotations, "<4f"),
                    ("scale", bone_anim.scales, "<3f"),
                ):
                    if not values:
                        continue
                    out_view = add_view(b"".join(struct.pack(fmt, *v) for v in values))
                    accessors.append(
                        {
                            "bufferView": out_view,
                            "componentType": 5126,
                            "count": len(values),
                            "type": "VEC4" if path == "rotation" else "VEC3",
                        }
                    )
                    samplers_a.append(
                        {
                            "input": time_accessor,
                            "output": len(accessors) - 1,
                            "interpolation": "LINEAR",
                        }
                    )
                    channels_a.append(
                        {
                            "sampler": len(samplers_a) - 1,
                            "target": {"node": node_index, "path": path},
                        }
                    )
            if channels_a:
                gltf_animations.append(
                    {"name": motion.name, "samplers": samplers_a, "channels": channels_a}
                )

    mesh_node_indices = [i for i, n in enumerate(nodes) if "mesh" in n]
    gltf = {
        "asset": {"version": "2.0", "generator": "RAE 3DS GFModel exporter"},
        "scene": 0,
        "scenes": [
            {"nodes": skeleton_roots + mesh_node_indices, "name": scene_name or model.name}
        ],
        "nodes": nodes,
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": [{"byteLength": len(bin_blob)}],
        "materials": materials,
    }
    if skins:
        gltf["skins"] = skins
    if gltf_animations:
        gltf["animations"] = gltf_animations
    if gltf_textures:
        gltf["samplers"] = samplers
        gltf["images"] = images
        gltf["textures"] = gltf_textures

    json_blob = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(json_blob) % 4:
        json_blob += b" "
    while len(bin_blob) % 4:
        bin_blob.append(0)

    total = 12 + 8 + len(json_blob) + 8 + len(bin_blob)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<II", len(json_blob), 0x4E4F534A))
        fh.write(json_blob)
        fh.write(struct.pack("<II", len(bin_blob), 0x004E4942))
        fh.write(bytes(bin_blob))
    return out_path
