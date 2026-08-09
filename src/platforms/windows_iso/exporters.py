"""Island-owned GLB and COLLADA exporters for decoded Windows ISO meshes."""
from __future__ import annotations

import json
from pathlib import Path
import struct
from typing import Sequence
from xml.etree import ElementTree as ET

import numpy as np

from .animated_gltf import _Glb
from .gltf.apply import apply_platform_glb_policy
from .materials import gltf_alpha_properties
from .smo import SmoMesh


_COLLADA_NS = "http://www.collada.org/2005/11/COLLADASchema"
ET.register_namespace("", _COLLADA_NS)


def _q(tag: str) -> str:
    return f"{{{_COLLADA_NS}}}{tag}"


def _y_up(values: tuple[tuple[float, float, float], ...]) -> np.ndarray:
    source = np.asarray(values, dtype=np.float32)
    return np.column_stack((source[:, 0], source[:, 2], -source[:, 1]))


def _float_text(values: np.ndarray) -> str:
    return " ".join(format(float(value), ".9g") for value in values.reshape(-1))


def write_glb(
    mesh: SmoMesh,
    output: Path,
    *,
    texture_png: Path | None = None,
    texture_pngs: Sequence[Path | None] | None = None,
) -> Path:
    """Write a self-contained glTF 2.0 binary preview."""
    supplied = list(texture_pngs or ([texture_png] if texture_png else []))
    glb = _Glb()
    face_materials = np.asarray(mesh.face_materials, dtype=np.int64)
    vertices = _y_up(mesh.vertices).astype(np.float32)
    normals = _y_up(mesh.normals)
    lengths = np.linalg.norm(normals, axis=1)
    invalid_normals = lengths < 1e-12
    lengths[invalid_normals] = 1.0
    normals = (normals / lengths[:, None]).astype(np.float32)
    normals[invalid_normals] = (0.0, 1.0, 0.0)
    uvs = np.asarray(mesh.uvs, dtype=np.float32)
    faces = np.asarray(mesh.faces, dtype=np.uint32)
    attributes = {
        "POSITION": glb.array(vertices, "VEC3", target=34962, bounds=True),
        "NORMAL": glb.array(normals, "VEC3", target=34962),
        "TEXCOORD_0": glb.array(uvs, "VEC2", target=34962),
    }
    images: list[dict] = []
    textures: list[dict] = []
    materials: list[dict] = []
    primitives: list[dict] = []
    slots = sorted(set(mesh.face_materials))
    for slot in slots:
        selected = faces[face_materials == slot]
        if not len(selected):
            continue
        texture = supplied[slot] if slot < len(supplied) else supplied[0] if supplied else None
        pbr: dict = {
            "baseColorFactor": (
                [1.0, 1.0, 1.0, 1.0]
                if texture is not None
                else [0.8, 0.8, 0.8, 1.0]
            ),
            "metallicFactor": 0.0,
            "roughnessFactor": 1.0,
        }
        if texture is not None:
            image_index = len(images)
            images.append({
                "bufferView": glb.view(Path(texture).read_bytes()),
                "mimeType": "image/png",
            })
            textures.append({"source": image_index, "sampler": 0})
            pbr["baseColorTexture"] = {"index": len(textures) - 1}
        material_index = len(materials)
        material = {
            "name": f"mpe_material_{slot}",
            "pbrMetallicRoughness": pbr,
            "doubleSided": True,
        }
        material.update(gltf_alpha_properties(texture, uvs=uvs, faces=selected))
        materials.append(material)
        primitives.append({
            "attributes": attributes,
            "indices": glb.array(selected.reshape(-1), "SCALAR", target=34963),
            "material": material_index,
        })

    document: dict = {
        "asset": {"version": "2.0", "generator": "RAE Windows ISO"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Marine Park Empire model", "mesh": 0}],
        "meshes": [{"name": "Marine Park Empire model", "primitives": primitives}],
        "materials": materials,
        "buffers": [{"byteLength": len(glb.binary)}],
        "bufferViews": glb.views,
        "accessors": glb.accessors,
    }
    if images:
        document["images"] = images
        document["textures"] = textures
        document["samplers"] = [{
            "magFilter": 9729,
            "minFilter": 9987,
            "wrapS": 10497,
            "wrapT": 10497,
        }]
    encoded = json.dumps(document, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded += b" " * ((-len(encoded)) % 4)
    binary = bytes(glb.binary) + b"\0" * ((-len(glb.binary)) % 4)
    body = (
        struct.pack("<I4s", len(encoded), b"JSON") + encoded
        + struct.pack("<I4s", len(binary), b"BIN\0") + binary
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body)
    apply_platform_glb_policy(output)
    return output


def _source(
    parent: ET.Element,
    source_id: str,
    values: np.ndarray,
    params: tuple[str, ...],
) -> None:
    source = ET.SubElement(parent, _q("source"), {"id": source_id})
    flat = values.reshape(-1)
    array_id = f"{source_id}-array"
    array = ET.SubElement(
        source,
        _q("float_array"),
        {"id": array_id, "count": str(flat.size)},
    )
    array.text = _float_text(values)
    technique = ET.SubElement(source, _q("technique_common"))
    accessor = ET.SubElement(
        technique,
        _q("accessor"),
        {
            "source": f"#{array_id}",
            "count": str(len(values)),
            "stride": str(len(params)),
        },
    )
    for name in params:
        ET.SubElement(accessor, _q("param"), {"name": name, "type": "float"})


def write_dae(
    mesh: SmoMesh,
    output: Path,
    *,
    texture_filename: str | None = None,
    texture_filenames: Sequence[str | None] | None = None,
) -> Path:
    """Write one clean COLLADA geometry with an optional external PNG texture."""
    root = ET.Element(_q("COLLADA"), {"version": "1.4.1"})
    asset = ET.SubElement(root, _q("asset"))
    contributor = ET.SubElement(asset, _q("contributor"))
    ET.SubElement(contributor, _q("authoring_tool")).text = "RAE Windows ISO"
    ET.SubElement(asset, _q("unit"), {"name": "centimeter", "meter": "0.01"})
    ET.SubElement(asset, _q("up_axis")).text = "Y_UP"

    supplied = list(texture_filenames or ([texture_filename] if texture_filename else []))
    material_count = max(mesh.face_materials, default=0) + 1
    if supplied:
        images = ET.SubElement(root, _q("library_images"))
        for slot, filename in enumerate(supplied):
            if not filename:
                continue
            image = ET.SubElement(
                images,
                _q("image"),
                {"id": f"mpe-image-{slot}", "name": f"mpe-image-{slot}"},
            )
            ET.SubElement(image, _q("init_from")).text = filename

    effects = ET.SubElement(root, _q("library_effects"))
    materials = ET.SubElement(root, _q("library_materials"))
    for slot in range(material_count):
        filename = supplied[slot] if slot < len(supplied) else supplied[0] if supplied else None
        effect = ET.SubElement(effects, _q("effect"), {"id": f"mpe-effect-{slot}"})
        profile = ET.SubElement(effect, _q("profile_COMMON"))
        if filename:
            image_slot = slot if slot < len(supplied) else 0
            surface_param = ET.SubElement(
                profile,
                _q("newparam"),
                {"sid": f"mpe-surface-{slot}"},
            )
            surface = ET.SubElement(surface_param, _q("surface"), {"type": "2D"})
            ET.SubElement(surface, _q("init_from")).text = f"mpe-image-{image_slot}"
            sampler_param = ET.SubElement(
                profile,
                _q("newparam"),
                {"sid": f"mpe-sampler-{slot}"},
            )
            sampler = ET.SubElement(sampler_param, _q("sampler2D"))
            ET.SubElement(sampler, _q("source")).text = f"mpe-surface-{slot}"
        technique = ET.SubElement(profile, _q("technique"), {"sid": "common"})
        phong = ET.SubElement(technique, _q("phong"))
        diffuse = ET.SubElement(phong, _q("diffuse"))
        if filename:
            ET.SubElement(
                diffuse,
                _q("texture"),
                {"texture": f"mpe-sampler-{slot}", "texcoord": "UVSET0"},
            )
        else:
            ET.SubElement(diffuse, _q("color")).text = "0.8 0.8 0.8 1"
        material = ET.SubElement(
            materials,
            _q("material"),
            {"id": f"mpe-material-{slot}"},
        )
        ET.SubElement(
            material,
            _q("instance_effect"),
            {"url": f"#mpe-effect-{slot}"},
        )

    geometries = ET.SubElement(root, _q("library_geometries"))
    geometry = ET.SubElement(geometries, _q("geometry"), {"id": "mpe-geometry"})
    collada_mesh = ET.SubElement(geometry, _q("mesh"))
    positions = _y_up(mesh.vertices)
    normals = _y_up(mesh.normals)
    # V3D and glTF both use a top-left texture origin. COLLADA consumers
    # conventionally use OpenGL's lower-left origin, so flip only at this
    # format boundary rather than corrupting the shared decoded mesh.
    raw_uvs = np.asarray(mesh.uvs, dtype=np.float32)
    uvs = np.column_stack((raw_uvs[:, 0], 1.0 - raw_uvs[:, 1]))
    _source(collada_mesh, "mpe-positions", positions, ("X", "Y", "Z"))
    _source(collada_mesh, "mpe-normals", normals, ("X", "Y", "Z"))
    _source(collada_mesh, "mpe-uvs", uvs, ("S", "T"))
    vertices = ET.SubElement(collada_mesh, _q("vertices"), {"id": "mpe-vertices"})
    ET.SubElement(
        vertices,
        _q("input"),
        {"semantic": "POSITION", "source": "#mpe-positions"},
    )
    for slot in range(material_count):
        slot_faces = [
            face for face, material_slot in zip(mesh.faces, mesh.face_materials) if material_slot == slot
        ]
        if not slot_faces:
            continue
        triangles = ET.SubElement(
            collada_mesh,
            _q("triangles"),
            {"count": str(len(slot_faces)), "material": f"mpe-material-symbol-{slot}"},
        )
        ET.SubElement(
            triangles,
            _q("input"),
            {"semantic": "VERTEX", "source": "#mpe-vertices", "offset": "0"},
        )
        ET.SubElement(
            triangles,
            _q("input"),
            {"semantic": "NORMAL", "source": "#mpe-normals", "offset": "1"},
        )
        ET.SubElement(
            triangles,
            _q("input"),
            {
                "semantic": "TEXCOORD",
                "source": "#mpe-uvs",
                "offset": "2",
                "set": "0",
            },
        )
        packed: list[str] = []
        for face in slot_faces:
            for index in face:
                packed.extend((str(index), str(index), str(index)))
        ET.SubElement(triangles, _q("p")).text = " ".join(packed)

    scenes = ET.SubElement(root, _q("library_visual_scenes"))
    visual_scene = ET.SubElement(scenes, _q("visual_scene"), {"id": "Scene"})
    node = ET.SubElement(visual_scene, _q("node"), {"id": "mpe-model", "name": "mpe-model"})
    instance = ET.SubElement(node, _q("instance_geometry"), {"url": "#mpe-geometry"})
    bind = ET.SubElement(instance, _q("bind_material"))
    common = ET.SubElement(bind, _q("technique_common"))
    for slot in range(material_count):
        instance_material = ET.SubElement(
            common,
            _q("instance_material"),
            {
                "symbol": f"mpe-material-symbol-{slot}",
                "target": f"#mpe-material-{slot}",
            },
        )
        ET.SubElement(
            instance_material,
            _q("bind_vertex_input"),
            {
                "semantic": "UVSET0",
                "input_semantic": "TEXCOORD",
                "input_set": "0",
            },
        )
    scene = ET.SubElement(root, _q("scene"))
    ET.SubElement(scene, _q("instance_visual_scene"), {"url": "#Scene"})

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output
