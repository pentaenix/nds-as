"""Self-contained GLB writer for 3DS GFModel meshes with embedded PNG textures."""
from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from pathlib import Path

from .gf import GfBone, GfMaterial, GfMesh, GfModel, GfTexture, GfTextureUnit
from .gltf.apply import apply_glb_policy
from .gltf.texture_alpha import (
    png_has_meaningful_transparency,
    texture_has_partial_alpha_channel,
)
from .motion import (
    EYE_SHEET_COLS,
    EYE_SHEET_ROWS,
    GfMotion,
    _eye_sheet_dims,
    eye_expression_frame_offsets,
    eye_expression_frame_translations,
    bake_motion,
    mesh_bind_visibility,
    visibility_track_export,
)


@dataclass(slots=True)
class FormVariantExport:
    id: str
    label: str
    model: GfModel
    textures: list[GfTexture]
    shiny_textures: list[GfTexture] | None = None
    geometry: str = "texture_only"


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
    """GF emissive mask overlays use an ``_Inc`` suffix or the name ``EyeInc``.

    Do not match names that merely contain ``Inc`` (e.g. Reshiram ``BodyBSpcInc``,
    ``BodyBInc01``) — those are separate multi-texture layers, not grayscale masks
    tinted by ``specular0`` like Kyogre's ``*_Neolant_Inc`` lines.
    """
    return name.endswith("_Inc") or name == "EyeInc"


def _mesh_draw_priority(mesh: GfMesh) -> tuple[int, str]:
    """Export sort key: body/skin first, inc accents, then eyes/mouth/iris last.

    Matches the RAE WebEngine preview (``renderOrder`` 1 → 2 → 3) so engines
    that draw in node/primitive order do not bury face layers under accents.
    """
    material_names = {sub.material_name for sub in mesh.submeshes}
    roles = {_material_role(name) for name in material_names}
    if "eye_iris" in roles:
        return (3, mesh.name)
    if "eye_sclera" in roles:
        return (2, mesh.name)
    lowered = mesh.name.lower()
    if any(token in lowered for token in ("eye", "iris", "mouth")):
        return (2, mesh.name)
    if lowered.endswith("_optmesh") and any("inc" in m.lower() for m in material_names):
        return (1, mesh.name)
    return (0, mesh.name)


def _material_nitro_alpha(mat: GfMaterial) -> float:
    """GF stores unused alpha as 0 in blend/diffuse — do not treat that as invisible."""
    if mat.diffuse is not None:
        dr, dg, db, da = mat.diffuse
        # Mask / multi-unit layers (e.g. vine trunks) use diffuse RGB=(0,0,0) with a
        # non-zero alpha channel for the TEV pipeline, not glTF transparency.
        if da > 0 and (dr, dg, db) == (0, 0, 0):
            if mat.blend is None or mat.blend[3] == 0:
                return 1.0
    candidates: list[int] = []
    if mat.blend is not None and mat.blend[3] > 0:
        candidates.append(mat.blend[3])
    if mat.diffuse is not None and mat.diffuse[3] > 0:
        candidates.append(mat.diffuse[3])
    if not candidates:
        return 1.0
    return max(0.0, min(1.0, min(candidates) / 255.0))


def _diffuse_tint(mat: GfMaterial) -> tuple[int, int, int]:
    if mat.diffuse is None:
        return (255, 255, 255)
    dr, dg, db, _da = mat.diffuse
    if (dr, dg, db) == (0, 0, 0):
        return (255, 255, 255)
    return (dr, dg, db)


def _uses_additive_blend(mat: GfMaterial, rgba: bytes | None = None) -> bool:
    """Additive only for GF glow overlays — mirrors DS glb_policy texture-alpha rules.

    On NDS, apicula tags ``textureAlpha`` (opaque / transparent / translucent) and
    ``apply_glb_policy`` downgrades false blends when the PNG has no real alpha.
    GF ``emission`` alpha>0 is *not* a blanket additive flag; use texture signals first.
    """
    if mat.emission is None or mat.emission[3] == 0:
        return False
    if _is_incandescent_material(mat.name):
        return False
    if rgba is None or len(rgba) < 4:
        return False

    alphas = rgba[3::4]
    total = len(alphas) or 1
    meaningful = sum(1 for a in alphas if a < 128) / total >= 0.005
    partial = any(8 < a < 247 for a in alphas)
    er, eg, eb, _ea = mat.emission

    if not meaningful and not partial:
        # DS analog: textureAlpha opaque → solid; only dark wave masks glow additively.
        if (er, eg, eb) == (255, 255, 255) and _texture_is_grayscale(rgba):
            rs, gs, bs = rgba[0::4], rgba[1::4], rgba[2::4]
            return max(rs) - min(rs) >= 40 or max(gs) - min(gs) >= 40 or max(bs) - min(bs) >= 40
        return False

    if partial:
        # DS analog: textureAlpha translucent → alpha blend, not additive.
        return False

    # Cutout transparency → MASK via classify (not additive).
    return False


def _bake_gf_texture_colors(rgba: bytes, mat: GfMaterial) -> bytes:
    """Approximate GF diffuse tint on grayscale masks. Emission stays in the TEV."""
    dr, dg, db = _diffuse_tint(mat)
    if _is_incandescent_material(mat.name) and mat.specular0 is not None:
        sr, sg, sb = (int(c) for c in mat.specular0[:3])
        if (sr, sg, sb) != (0, 0, 0):
            dr, dg, db = sr, sg, sb
    apply_diffuse = (dr, dg, db) != (255, 255, 255) and _texture_is_grayscale(rgba)
    if not apply_diffuse:
        return rgba
    out = bytearray(rgba)
    for i in range(0, len(out), 4):
        r, g, b, a = out[i], out[i + 1], out[i + 2], out[i + 3]
        r = min(255, r * dr // 255)
        g = min(255, g * dg // 255)
        b = min(255, b * db // 255)
        out[i : i + 4] = bytes((r, g, b, a))
    return bytes(out)


def _material_texture_key(mat: GfMaterial, tex_name: str) -> str:
    return f"{mat.name}|{tex_name}"


def _texture_alpha_kind(png: bytes) -> str:
    if texture_has_partial_alpha_channel(png):
        return "translucent"
    if png_has_meaningful_transparency(png):
        return "transparent"
    return "opaque"


def _texture_is_grayscale(rgba: bytes, tolerance: int = 10) -> bool:
    for i in range(0, len(rgba), 4):
        r, g, b = rgba[i], rgba[i + 1], rgba[i + 2]
        if abs(int(r) - int(g)) > tolerance:
            return False
        if abs(int(r) - int(b)) > tolerance:
            return False
        if abs(int(g) - int(b)) > tolerance:
            return False
    return True


def _bake_mesh_uv(
    u: float,
    v: float,
    sx: float,
    sy: float,
    tx: float,
    ty: float,
) -> tuple[float, float]:
    """Apply GF texture-unit transform; flip U when scale_x is negative (iris)."""
    if sx < 0.0:
        u_coord = (1.0 - u) * abs(sx) + tx
    else:
        u_coord = u * sx + tx
    v_coord = 1.0 - (v * sy + ty)
    return u_coord, v_coord


def _bake_eye_sclera_uv(u: float, v: float, sy: float) -> tuple[float, float]:
    """Eye sclera: keep raw model U so mirror-wrap can separate left/right eyes.

    GF scale/translation on U stay on ``texture.repeat`` / ``texture.offset`` at
    runtime. Only V is baked (bind TY=0) so row shifts use ``offset.y``.
    """
    return u, 1.0 - (v * sy)


def _material_role(name: str) -> str | None:
    """GF Pokémon expression layers: Eye/LEye/REye/Mouth sclera sheets; *Iris pupils."""
    if name in ("Eye", "LEye", "REye", "Mouth"):
        return "eye_sclera"
    if name.endswith("Iris") or name.casefold().endswith("iris"):
        return "eye_iris"
    return None


def _attach_material_extras(
    entry: dict,
    mat: GfMaterial,
    texture_kind: str,
    *,
    additive: bool = False,
    uv_unit: tuple[float, float, float, float] | None = None,
    wrap_uv: tuple[int, int] | None = None,
) -> None:
    nitro_alpha = _material_nitro_alpha(mat)
    extras = entry.setdefault("extras", {})
    rae = extras.setdefault("rae", {})
    nitro = rae.setdefault("nitro", {})
    nitro["alpha"] = nitro_alpha
    nitro["textureAlpha"] = texture_kind
    if additive:
        nitro["blendMode"] = "additive"
    role = _material_role(mat.name)
    if role is not None:
        rae["materialRole"] = role
    if uv_unit is None:
        return
    sx, sy, tx, ty = uv_unit
    tex_unit: dict = {
        "scale": [sx, sy],
        "translation": [tx, ty],
    }
    if wrap_uv is not None:
        tex_unit["wrap"] = list(wrap_uv)
    if role == "eye_sclera":
        cols, rows = _eye_sheet_dims(sx, sy)
        translations = eye_expression_frame_translations(
            sx, sy, tx, ty, cols=cols, rows=rows
        )
        offsets = eye_expression_frame_offsets(sx, sy, tx, ty, cols=cols, rows=rows)
        rae["eyeSheet"] = {
            "cols": cols,
            "rows": rows,
            "uvLayout": "raw_u",
            **tex_unit,
        }
        rae["eyeExpression"] = {
            "frameCount": cols * rows,
            "defaultFrame": 0,
            "frameTranslations": translations,
            "frameOffsets": offsets,
        }


def write_model_glb(
    model: GfModel,
    textures: list[GfTexture],
    out_path: str | Path,
    *,
    scene_name: str | None = None,
    animations: list[GfMotion] | None = None,
    shiny_textures: list[GfTexture] | None = None,
    default_texture_variant: str = "normal",
    default_form_variant: str | None = None,
    form_variants: list[FormVariantExport] | None = None,
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
    tex_objects = {tex.name: tex for tex in textures}
    png_by_key: dict[str, int] = {}
    texture_alpha_kind: dict[str, str] = {}
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

    texture_index_by_name: dict[str, int] = {}
    texture_key_suffix = ""
    active_material_model = model

    def image_for(mat: GfMaterial, tex_name: str, texture_objects: dict[str, GfTexture]) -> int | None:
        key = _material_texture_key(mat, tex_name) + texture_key_suffix
        if key in png_by_key:
            return png_by_key[key]
        tex = texture_objects.get(tex_name)
        if tex is None:
            return None
        try:
            rgba = _bake_gf_texture_colors(tex.decode_rgba(), mat)
        except Exception:
            return None
        from .pica import rgba_to_png

        png = rgba_to_png(rgba, tex.width, tex.height)
        view_index = add_view(png)
        images.append({"bufferView": view_index, "mimeType": "image/png", "name": key})
        png_by_key[key] = len(images) - 1
        texture_alpha_kind[key] = _texture_alpha_kind(png)
        return png_by_key[key]

    def texture_for(
        mat: GfMaterial,
        name: str,
        wrap_u: int,
        wrap_v: int,
        texture_objects: dict[str, GfTexture],
    ) -> int | None:
        source = image_for(mat, name, texture_objects)
        if source is None:
            return None
        key = f"{mat.name}|{name}|{wrap_u}|{wrap_v}{texture_key_suffix}"
        if key not in texture_index_by_name:
            gltf_textures.append(
                {"sampler": sampler_for(wrap_u, wrap_v), "source": source, "name": name}
            )
            texture_index_by_name[key] = len(gltf_textures) - 1
        return texture_index_by_name[key]

    # -- materials -------------------------------------------------------------
    material_index_by_name: dict[str, int] = {}
    uv_transform_by_material: dict[str, tuple[float, float, float, float]] = {}

    def _append_materials(
        tex_objects: dict[str, GfTexture],
        *,
        material_model: GfModel | None = None,
        material_name_suffix: str = "",
    ) -> dict[str, int]:
        nonlocal active_material_model
        material_source = material_model or model
        active_material_model = material_source
        index_by_name: dict[str, int] = {}
        for mat in material_source.materials:
            unit = None
            entry: dict = {
                "name": f"{mat.name}{material_name_suffix}",
                "pbrMetallicRoughness": {
                    "metallicFactor": 0.0,
                    "roughnessFactor": 0.9,
                },
                "doubleSided": True,
            }
            albedo = next(
                (name for name in mat.texture_names if name in tex_objects),
                None,
            )
            tex_key = _material_texture_key(mat, albedo) + texture_key_suffix if albedo else ""
            nitro_alpha = _material_nitro_alpha(mat)
            albedo_rgba: bytes | None = None
            if albedo is not None and albedo in tex_objects:
                try:
                    albedo_rgba = tex_objects[albedo].decode_rgba()
                except Exception:
                    albedo_rgba = None
            additive = _uses_additive_blend(mat, albedo_rgba)
            unit = None
            if albedo is not None:
                unit = next((u for u in mat.texture_units if u.name == albedo), None)
                wrap_u, wrap_v = (unit.wrap_u, unit.wrap_v) if unit else (2, 2)
                tex_index = texture_for(mat, albedo, wrap_u, wrap_v, tex_objects)
                tex_kind = texture_alpha_kind.get(tex_key, "opaque")
                if tex_index is not None:
                    entry["pbrMetallicRoughness"]["baseColorTexture"] = {"index": tex_index}
                if additive or tex_kind in ("translucent", "transparent") or nitro_alpha < 0.999:
                    entry["alphaMode"] = "BLEND"
                if nitro_alpha < 0.999:
                    entry["pbrMetallicRoughness"]["baseColorFactor"] = [1.0, 1.0, 1.0, nitro_alpha]
                if _is_incandescent_material(mat.name) and mat.specular0 is not None:
                    red, green, blue = (int(c) for c in mat.specular0[:3])
                    if (red, green, blue) != (0, 0, 0):
                        entry["pbrMetallicRoughness"]["baseColorFactor"] = [
                            red / 255.0,
                            green / 255.0,
                            blue / 255.0,
                            1.0,
                        ]
                if unit is not None and mat.name not in uv_transform_by_material:
                    uv_transform_by_material[mat.name] = (
                        unit.scale[0],
                        unit.scale[1],
                        unit.translation[0],
                        unit.translation[1],
                    )
            elif nitro_alpha < 0.999:
                entry["alphaMode"] = "BLEND"
                entry["pbrMetallicRoughness"]["baseColorFactor"] = [1.0, 1.0, 1.0, nitro_alpha]
                tex_kind = "opaque"
            else:
                tex_kind = "opaque"
            uv_unit = uv_transform_by_material.get(mat.name)
            wrap_pair = (unit.wrap_u, unit.wrap_v) if albedo is not None and unit is not None else None
            _attach_material_extras(
                entry,
                mat,
                tex_kind,
                additive=additive,
                uv_unit=uv_unit,
                wrap_uv=wrap_pair,
            )
            index_by_name[mat.name] = len(materials)
            materials.append(entry)
        return index_by_name

    texture_key_suffix = ""
    material_index_by_name = _append_materials(tex_objects)
    shiny_material_index_by_name: dict[str, int] = {}
    if shiny_textures:
        shiny_objects = {tex.name: tex for tex in shiny_textures}
        texture_key_suffix = "|shiny"
        shiny_material_index_by_name = _append_materials(shiny_objects)
        for mat_name, normal_index in material_index_by_name.items():
            shiny_index = shiny_material_index_by_name.get(mat_name)
            if shiny_index is None:
                continue
            materials[normal_index].setdefault("extras", {}).setdefault("rae", {})[
                "shinyMaterialIndex"
            ] = shiny_index
    form_material_index_by_id: dict[str, dict[str, int]] = {}
    form_shiny_material_index_by_id: dict[str, dict[str, int]] = {}
    active_form_variants = form_variants or []
    for form in active_form_variants:
        form_objects = {tex.name: tex for tex in form.textures}
        texture_key_suffix = f"|form:{form.id}"
        form_materials = _append_materials(
            form_objects,
            material_model=form.model,
            material_name_suffix=f"__form_{form.id}",
        )
        form_material_index_by_id[form.id] = form_materials
        if form.shiny_textures:
            shiny_objects = {tex.name: tex for tex in form.shiny_textures}
            texture_key_suffix = f"|form:{form.id}|shiny"
            shiny_materials = _append_materials(
                shiny_objects,
                material_model=form.model,
                material_name_suffix=f"__form_{form.id}_shiny",
            )
            form_shiny_material_index_by_id[form.id] = shiny_materials
            for mat_name, normal_index in form_materials.items():
                shiny_index = shiny_materials.get(mat_name)
                if shiny_index is None:
                    continue
                materials[normal_index].setdefault("extras", {}).setdefault("rae", {})[
                    "shinyMaterialIndex"
                ] = shiny_index
    texture_key_suffix = ""
    active_material_model = model
    texture_only_forms = [
        form for form in active_form_variants if form.geometry == "texture_only"
    ]
    geometry_forms = [
        form for form in active_form_variants if form.geometry != "texture_only"
    ]
    for mat_name, normal_index in material_index_by_name.items():
        form_indices: dict[str, int] = {}
        for form in texture_only_forms:
            index = form_material_index_by_id.get(form.id, {}).get(mat_name)
            if index is not None:
                form_indices[form.id] = index
        if form_indices:
            materials[normal_index].setdefault("extras", {}).setdefault("rae", {})[
                "formMaterialIndices"
            ] = form_indices

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
    mesh_materials: dict[str, list[str]] = {}
    for mesh in model.meshes:
        mesh_materials[mesh.name] = [sub.material_name for sub in mesh.submeshes]
    bind_visibility = mesh_bind_visibility(
        [mesh.name for mesh in model.meshes],
        animations or [],
        opt_mesh_materials=mesh_materials,
    )

    default_form_id = default_form_variant or "00"
    base_geometry_forms = [default_form_id] + [form.id for form in texture_only_forms]

    for mesh in sorted(model.meshes, key=_mesh_draw_priority):
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
                mat_role = _material_role(sub.material_name)
                sx, sy, tx, ty = uv_transform_by_material.get(
                    sub.material_name, (1.0, 1.0, 0.0, 0.0)
                )
                if mat_role == "eye_sclera":
                    uv_data = b"".join(
                        struct.pack("<2f", *_bake_eye_sclera_uv(u, v, sy))
                        for u, v in sub.uvs
                    )
                else:
                    uv_data = b"".join(
                        struct.pack(
                            "<2f",
                            *_bake_mesh_uv(u, v, sx, sy, tx, ty),
                        )
                        for u, v in sub.uvs
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
        draw_priority, _ = _mesh_draw_priority(mesh)
        mesh_extras = mesh_node.setdefault("extras", {}).setdefault("rae", {})
        if mesh.name in bind_visibility:
            mesh_extras["defaultVisible"] = bind_visibility[mesh.name]
        if draw_priority >= 2:
            mesh_extras["renderOrder"] = draw_priority
        if geometry_forms:
            mesh_extras["visibleForForms"] = base_geometry_forms
        if has_skinning and any("JOINTS_0" in p["attributes"] for p in primitives):
            mesh_node["skin"] = 0
        nodes.append(mesh_node)

    for form in geometry_forms:
        material_map = form_material_index_by_id.get(form.id, {})
        for mesh in sorted(form.model.meshes, key=_mesh_draw_priority):
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
                        normals.append(
                            (n[0] / length, n[1] / length, n[2] / length)
                            if length > 1e-6
                            else (0.0, 1.0, 0.0)
                        )
                    nrm_view = add_view(
                        b"".join(struct.pack("<3f", *n) for n in normals),
                        target=34962,
                    )
                    accessors.append(
                        {
                            "bufferView": nrm_view,
                            "componentType": 5126,
                            "count": count,
                            "type": "VEC3",
                        }
                    )
                    attributes["NORMAL"] = len(accessors) - 1

                if len(sub.uvs) == count:
                    mat_role = _material_role(sub.material_name)
                    sx, sy, tx, ty = uv_transform_by_material.get(
                        sub.material_name, (1.0, 1.0, 0.0, 0.0)
                    )
                    if mat_role == "eye_sclera":
                        uv_data = b"".join(
                            struct.pack("<2f", *_bake_eye_sclera_uv(u, v, sy))
                            for u, v in sub.uvs
                        )
                    else:
                        uv_data = b"".join(
                            struct.pack("<2f", *_bake_mesh_uv(u, v, sx, sy, tx, ty))
                            for u, v in sub.uvs
                        )
                    uv_view = add_view(uv_data, target=34962)
                    accessors.append(
                        {
                            "bufferView": uv_view,
                            "componentType": 5126,
                            "count": count,
                            "type": "VEC2",
                        }
                    )
                    attributes["TEXCOORD_0"] = len(accessors) - 1

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
                mat_index = material_map.get(sub.material_name)
                if mat_index is not None:
                    primitive["material"] = mat_index
                primitives.append(primitive)
            if not primitives:
                continue
            meshes.append({"name": f"{mesh.name}__form_{form.id}", "primitives": primitives})
            mesh_node = {
                "mesh": len(meshes) - 1,
                "name": f"{mesh.name}__form_{form.id}",
            }
            draw_priority, _ = _mesh_draw_priority(mesh)
            mesh_extras = mesh_node.setdefault("extras", {}).setdefault("rae", {})
            mesh_extras["defaultVisible"] = form.id == default_form_id
            mesh_extras["visibleForForms"] = [form.id]
            if draw_priority >= 2:
                mesh_extras["renderOrder"] = draw_priority
            nodes.append(mesh_node)

    # -- animations --------------------------------------------------------------
    gltf_animations: list[dict] = []
    if animations and model.bones:
        rest_pose = {b.name: (b.scale, b.rotation, b.translation) for b in model.bones}
        for motion in animations:
            times, baked = bake_motion(motion, rest_pose)
            if len(times) < 2:
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
            if channels_a or motion.visibility_tracks:
                anim_entry: dict = {
                    "name": motion.name,
                    "samplers": samplers_a,
                    "channels": channels_a,
                }
                if motion.visibility_tracks:
                    anim_entry.setdefault("extras", {}).setdefault("rae", {})[
                        "meshVisibility"
                    ] = {
                        track.name: visibility_track_export(track)
                        for track in motion.visibility_tracks
                    }
                if channels_a or motion.visibility_tracks:
                    gltf_animations.append(anim_entry)

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
    has_texture_variants = bool(shiny_material_index_by_name) or any(
        form.id in form_shiny_material_index_by_id for form in active_form_variants
    )
    if has_texture_variants or active_form_variants:
        default_id = "shiny" if default_texture_variant == "shiny" else "normal"
        rae_root = gltf.setdefault("extras", {}).setdefault("rae", {})
        texture_variants = {
            "default": default_id,
            "options": [
                {"id": "normal", "label": "Normal"},
                {"id": "shiny", "label": "Shiny"},
            ],
        }
        if has_texture_variants:
            rae_root["textureVariants"] = texture_variants
        appearance_default: dict = {"texture": default_id}
        axes: list[dict] = []
        if active_form_variants:
            form_options = [{"id": default_form_id, "label": f"Form {default_form_id}"}] + [
                {"id": form.id, "label": form.label} for form in active_form_variants
            ]
            appearance_default["form"] = default_form_id
            axes.append(
                {
                    "id": "form",
                    "label": "Form",
                    "default": default_form_id,
                    "options": form_options,
                }
            )
            rae_root["speciesVariants"] = {
                "default": {"form": default_form_id, "shiny": default_id == "shiny"},
                "forms": form_options,
            }
        if has_texture_variants:
            axes.append(
                {
                    "id": "texture",
                    "label": "Color",
                    "default": default_id,
                    "options": texture_variants["options"],
                }
            )
        rae_root["appearanceVariants"] = {
            "default": appearance_default,
            "axes": axes,
        }

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
    apply_glb_policy(out_path)
    return out_path
