"""Dependency-free GLB scene composition for Nintendo DS assets."""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass
from pathlib import Path

from .glb_io import GlbData, read_glb


@dataclass(frozen=True)
class GlbScenePart:
    path: Path
    name: str
    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    quarter_turns: int = 0
    rotation_degrees: float | None = None


def _offset_index(value, offset: int):
    return int(value) + offset if isinstance(value, int) else value


def _remap_texture_info(info: object, texture_offset: int) -> None:
    if isinstance(info, dict) and isinstance(info.get("index"), int):
        info["index"] += texture_offset


def _remap_material(material: dict, texture_offset: int) -> None:
    pbr = material.get("pbrMetallicRoughness")
    if isinstance(pbr, dict):
        _remap_texture_info(pbr.get("baseColorTexture"), texture_offset)
        _remap_texture_info(pbr.get("metallicRoughnessTexture"), texture_offset)
    for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
        _remap_texture_info(material.get(key), texture_offset)


def _component_roots(gltf: dict) -> list[int]:
    scenes = gltf.get("scenes") or []
    scene_index = int(gltf.get("scene") or 0)
    if 0 <= scene_index < len(scenes) and isinstance(scenes[scene_index], dict):
        roots = scenes[scene_index].get("nodes") or []
        return [int(value) for value in roots if isinstance(value, int)]
    nodes = gltf.get("nodes") or []
    children = {
        int(child)
        for node in nodes
        if isinstance(node, dict)
        for child in (node.get("children") or [])
        if isinstance(child, int)
    }
    return [index for index in range(len(nodes)) if index not in children]


def _placement_matrix(
    translation: tuple[float, float, float],
    quarter_turns: int,
    rotation_degrees: float | None = None,
) -> list[float]:
    degrees = float(rotation_degrees) if rotation_degrees is not None else (int(quarter_turns) & 3) * 90.0
    angle = math.radians(degrees)
    cosine = round(math.cos(angle), 12)
    sine = round(math.sin(angle), 12)
    x, y, z = (float(value) for value in translation)
    # glTF matrices are column-major. This is translation * Y rotation.
    return [
        cosine, 0.0, -sine, 0.0,
        0.0, 1.0, 0.0, 0.0,
        sine, 0.0, cosine, 0.0,
        x, y, z, 1.0,
    ]


def compose_glb_scenes(parts: list[GlbScenePart], output: Path) -> Path:
    """Merge self-contained GLBs and parent each one under a placement node."""
    if not parts:
        raise ValueError("At least one GLB scene part is required")

    out: dict = {
        "asset": {"version": "2.0", "generator": "RAE NDS GLB composer"},
        "scene": 0,
        "scenes": [{"name": "NDS composed map", "nodes": []}],
        "buffers": [{"byteLength": 0}],
    }
    binary = bytearray()
    extensions_used: list[str] = []
    extensions_required: list[str] = []

    for part_index, part in enumerate(parts):
        source = read_glb(part.path)
        gltf = copy.deepcopy(source.json)
        map_motion = ((gltf.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion")
        if map_motion and not ((out.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion"):
            out.setdefault("extras", {}).setdefault("rae", {})["mapMaterialMotion"] = copy.deepcopy(map_motion)
        while len(binary) % 4:
            binary.append(0)
        binary_offset = len(binary)
        binary.extend(source.bin_chunk)

        offsets = {
            "bufferViews": len(out.get("bufferViews") or []),
            "accessors": len(out.get("accessors") or []),
            "images": len(out.get("images") or []),
            "samplers": len(out.get("samplers") or []),
            "textures": len(out.get("textures") or []),
            "materials": len(out.get("materials") or []),
            "meshes": len(out.get("meshes") or []),
            "cameras": len(out.get("cameras") or []),
            "skins": len(out.get("skins") or []),
            "nodes": len(out.get("nodes") or []),
            "animations": len(out.get("animations") or []),
        }

        buffer_views = copy.deepcopy(gltf.get("bufferViews") or [])
        for view in buffer_views:
            if not isinstance(view, dict):
                continue
            view["buffer"] = 0
            view["byteOffset"] = int(view.get("byteOffset") or 0) + binary_offset
        out.setdefault("bufferViews", []).extend(buffer_views)

        accessors = copy.deepcopy(gltf.get("accessors") or [])
        for accessor in accessors:
            if isinstance(accessor, dict) and isinstance(accessor.get("bufferView"), int):
                accessor["bufferView"] += offsets["bufferViews"]
            sparse = accessor.get("sparse") if isinstance(accessor, dict) else None
            if isinstance(sparse, dict):
                for body_name in ("indices", "values"):
                    body = sparse.get(body_name)
                    if isinstance(body, dict) and isinstance(body.get("bufferView"), int):
                        body["bufferView"] += offsets["bufferViews"]
        out.setdefault("accessors", []).extend(accessors)

        images = copy.deepcopy(gltf.get("images") or [])
        for image in images:
            if isinstance(image, dict) and isinstance(image.get("bufferView"), int):
                image["bufferView"] += offsets["bufferViews"]
        out.setdefault("images", []).extend(images)
        out.setdefault("samplers", []).extend(copy.deepcopy(gltf.get("samplers") or []))

        textures = copy.deepcopy(gltf.get("textures") or [])
        for texture in textures:
            if not isinstance(texture, dict):
                continue
            if isinstance(texture.get("source"), int):
                texture["source"] += offsets["images"]
            if isinstance(texture.get("sampler"), int):
                texture["sampler"] += offsets["samplers"]
            basisu = (texture.get("extensions") or {}).get("KHR_texture_basisu")
            if isinstance(basisu, dict) and isinstance(basisu.get("source"), int):
                basisu["source"] += offsets["images"]
        out.setdefault("textures", []).extend(textures)

        materials = copy.deepcopy(gltf.get("materials") or [])
        for material in materials:
            if isinstance(material, dict):
                _remap_material(material, offsets["textures"])
        out.setdefault("materials", []).extend(materials)

        meshes = copy.deepcopy(gltf.get("meshes") or [])
        for mesh in meshes:
            if not isinstance(mesh, dict):
                continue
            for primitive in mesh.get("primitives") or []:
                if not isinstance(primitive, dict):
                    continue
                if isinstance(primitive.get("indices"), int):
                    primitive["indices"] += offsets["accessors"]
                if isinstance(primitive.get("material"), int):
                    primitive["material"] += offsets["materials"]
                attributes = primitive.get("attributes") or {}
                for key, value in list(attributes.items()):
                    attributes[key] = _offset_index(value, offsets["accessors"])
                for target in primitive.get("targets") or []:
                    if isinstance(target, dict):
                        for key, value in list(target.items()):
                            target[key] = _offset_index(value, offsets["accessors"])
        out.setdefault("meshes", []).extend(meshes)
        out.setdefault("cameras", []).extend(copy.deepcopy(gltf.get("cameras") or []))

        skins = copy.deepcopy(gltf.get("skins") or [])
        for skin in skins:
            if not isinstance(skin, dict):
                continue
            if isinstance(skin.get("inverseBindMatrices"), int):
                skin["inverseBindMatrices"] += offsets["accessors"]
            if isinstance(skin.get("skeleton"), int):
                skin["skeleton"] += offsets["nodes"]
            skin["joints"] = [
                int(value) + offsets["nodes"]
                for value in (skin.get("joints") or [])
                if isinstance(value, int)
            ]
        out.setdefault("skins", []).extend(skins)

        nodes = copy.deepcopy(gltf.get("nodes") or [])
        for node in nodes:
            if not isinstance(node, dict):
                continue
            if isinstance(node.get("mesh"), int):
                node["mesh"] += offsets["meshes"]
            if isinstance(node.get("camera"), int):
                node["camera"] += offsets["cameras"]
            if isinstance(node.get("skin"), int):
                node["skin"] += offsets["skins"]
            node["children"] = [
                int(value) + offsets["nodes"]
                for value in (node.get("children") or [])
                if isinstance(value, int)
            ]
        out.setdefault("nodes", []).extend(nodes)

        animations = copy.deepcopy(gltf.get("animations") or [])
        for animation in animations:
            if not isinstance(animation, dict):
                continue
            for sampler in animation.get("samplers") or []:
                if not isinstance(sampler, dict):
                    continue
                sampler["input"] = _offset_index(sampler.get("input"), offsets["accessors"])
                sampler["output"] = _offset_index(sampler.get("output"), offsets["accessors"])
            for channel in animation.get("channels") or []:
                target = channel.get("target") if isinstance(channel, dict) else None
                if isinstance(target, dict) and isinstance(target.get("node"), int):
                    target["node"] += offsets["nodes"]
            property_animation = (animation.get("extensions") or {}).get("EXT_property_animation")
            if isinstance(property_animation, dict):
                for channel in property_animation.get("channels") or []:
                    target = channel.get("target") if isinstance(channel, dict) else None
                    if not isinstance(target, str):
                        continue
                    channel["target"] = re.sub(
                        r"^/materials/(\d+)(?=/)",
                        lambda match: f"/materials/{int(match.group(1)) + offsets['materials']}",
                        target,
                    )
        out.setdefault("animations", []).extend(animations)

        roots = [value + offsets["nodes"] for value in _component_roots(gltf)]
        parent_index = len(out.get("nodes") or [])
        out.setdefault("nodes", []).append(
            {
                "name": part.name or f"part_{part_index:02d}",
                "children": roots,
                "matrix": _placement_matrix(part.translation, part.quarter_turns, part.rotation_degrees),
            }
        )
        out["scenes"][0]["nodes"].append(parent_index)

        for extension in gltf.get("extensionsUsed") or []:
            if extension not in extensions_used:
                extensions_used.append(extension)
        for extension in gltf.get("extensionsRequired") or []:
            if extension not in extensions_required:
                extensions_required.append(extension)

    for key in ("bufferViews", "accessors", "images", "samplers", "textures", "materials", "meshes", "cameras", "skins", "animations"):
        if not out.get(key):
            out.pop(key, None)
    out["buffers"][0]["byteLength"] = len(binary)
    if extensions_used:
        out["extensionsUsed"] = extensions_used
    if extensions_required:
        out["extensionsRequired"] = extensions_required
    output.parent.mkdir(parents=True, exist_ok=True)
    GlbData(json=out, bin_chunk=bytes(binary)).write(output)
    return output
