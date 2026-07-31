"""COLLADA 1.4.1 writer for Marine Park Empire skinned AM models."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree as ET

import numpy as np

from .am import Am1Mesh, AnimationSet, Skeleton, animation_clips_in_export_order
from .animated_gltf import _bind_geometry, _decompose, _evaluate, _target_matrix


_NS = "http://www.collada.org/2005/11/COLLADASchema"
ET.register_namespace("", _NS)


def _q(tag: str) -> str:
    return f"{{{_NS}}}{tag}"


def _text(values: Iterable[float]) -> str:
    return " ".join(format(float(value), ".9g") for value in values)


def _matrix_text(matrix: np.ndarray) -> str:
    # COLLADA matrices follow OpenGL column-major lexical ordering.
    return _text(matrix.T.reshape(-1))


def _float_source(parent: ET.Element, source_id: str, values: np.ndarray,
                  params: Sequence[tuple[str, str]]) -> None:
    values = np.asarray(values)
    source = ET.SubElement(parent, _q("source"), {"id": source_id})
    array_id = f"{source_id}-array"
    array = ET.SubElement(source, _q("float_array"), {"id": array_id, "count": str(values.size)})
    array.text = _text(values.reshape(-1))
    common = ET.SubElement(source, _q("technique_common"))
    accessor = ET.SubElement(common, _q("accessor"), {
        "source": f"#{array_id}", "count": str(len(values)),
        "stride": str(values.size // max(1, len(values))),
    })
    for name, kind in params:
        ET.SubElement(accessor, _q("param"), {"name": name, "type": kind})


def _name_source(parent: ET.Element, source_id: str, values: Sequence[str], param: str) -> None:
    source = ET.SubElement(parent, _q("source"), {"id": source_id})
    array_id = f"{source_id}-array"
    array = ET.SubElement(source, _q("Name_array"), {"id": array_id, "count": str(len(values))})
    array.text = " ".join(value.replace(" ", "_") for value in values)
    common = ET.SubElement(source, _q("technique_common"))
    accessor = ET.SubElement(common, _q("accessor"), {
        "source": f"#{array_id}", "count": str(len(values)), "stride": "1",
    })
    ET.SubElement(accessor, _q("param"), {"name": param, "type": "Name"})


def _materials(root: ET.Element, texture_filenames: Sequence[str | None], count: int) -> None:
    images = ET.SubElement(root, _q("library_images"))
    effects = ET.SubElement(root, _q("library_effects"))
    materials = ET.SubElement(root, _q("library_materials"))
    for slot in range(count):
        filename = (
            texture_filenames[slot] if slot < len(texture_filenames)
            else texture_filenames[0] if texture_filenames else None
        )
        if filename:
            image = ET.SubElement(images, _q("image"), {"id": f"image-{slot}"})
            ET.SubElement(image, _q("init_from")).text = filename
        effect = ET.SubElement(effects, _q("effect"), {"id": f"effect-{slot}"})
        profile = ET.SubElement(effect, _q("profile_COMMON"))
        if filename:
            surface_param = ET.SubElement(profile, _q("newparam"), {"sid": f"surface-{slot}"})
            surface = ET.SubElement(surface_param, _q("surface"), {"type": "2D"})
            ET.SubElement(surface, _q("init_from")).text = f"image-{slot}"
            sampler_param = ET.SubElement(profile, _q("newparam"), {"sid": f"sampler-{slot}"})
            sampler = ET.SubElement(sampler_param, _q("sampler2D"))
            ET.SubElement(sampler, _q("source")).text = f"surface-{slot}"
        technique = ET.SubElement(profile, _q("technique"), {"sid": "common"})
        phong = ET.SubElement(technique, _q("phong"))
        diffuse = ET.SubElement(phong, _q("diffuse"))
        if filename:
            ET.SubElement(diffuse, _q("texture"), {"texture": f"sampler-{slot}", "texcoord": "UVSET0"})
        else:
            ET.SubElement(diffuse, _q("color")).text = "0.8 0.8 0.8 1"
        material = ET.SubElement(materials, _q("material"), {"id": f"material-{slot}"})
        ET.SubElement(material, _q("instance_effect"), {"url": f"#effect-{slot}"})


def _geometry(root: ET.Element, mesh: Am1Mesh, positions: np.ndarray, normals: np.ndarray) -> None:
    library = ET.SubElement(root, _q("library_geometries"))
    geometry = ET.SubElement(library, _q("geometry"), {"id": "mpe-geometry", "name": "Marine Park Empire model"})
    body = ET.SubElement(geometry, _q("mesh"))
    _float_source(body, "positions", positions, (("X", "float"), ("Y", "float"), ("Z", "float")))
    _float_source(body, "normals", normals, (("X", "float"), ("Y", "float"), ("Z", "float")))
    # glTF can use V3D's top-left UVs directly; COLLADA consumers generally
    # expect lower-left texture coordinates.
    raw_uvs = np.asarray(mesh.uvs, dtype=np.float32)
    collada_uvs = np.column_stack((raw_uvs[:, 0], 1.0 - raw_uvs[:, 1]))
    _float_source(body, "uvs", collada_uvs, (("S", "float"), ("T", "float")))
    vertices = ET.SubElement(body, _q("vertices"), {"id": "vertices"})
    ET.SubElement(vertices, _q("input"), {"semantic": "POSITION", "source": "#positions"})
    face_slots = np.asarray(mesh.face_materials)
    faces = np.asarray(mesh.faces)
    for slot in sorted(set(mesh.face_materials)):
        selected = faces[face_slots == slot]
        triangles = ET.SubElement(body, _q("triangles"), {
            "count": str(len(selected)), "material": f"material-symbol-{slot}",
        })
        ET.SubElement(triangles, _q("input"), {"semantic": "VERTEX", "source": "#vertices", "offset": "0"})
        ET.SubElement(triangles, _q("input"), {"semantic": "NORMAL", "source": "#normals", "offset": "1"})
        ET.SubElement(triangles, _q("input"), {"semantic": "TEXCOORD", "source": "#uvs", "offset": "2", "set": "0"})
        packed = np.repeat(selected.reshape(-1), 3).reshape(-1, 3)
        ET.SubElement(triangles, _q("p")).text = " ".join(str(int(value)) for value in packed.reshape(-1))


def _controller(root: ET.Element, mesh: Am1Mesh, skeleton: Skeleton,
                inverse_bind: np.ndarray) -> None:
    library = ET.SubElement(root, _q("library_controllers"))
    controller = ET.SubElement(library, _q("controller"), {"id": "mpe-skin"})
    skin = ET.SubElement(controller, _q("skin"), {"source": "#mpe-geometry"})
    ET.SubElement(skin, _q("bind_shape_matrix")).text = _matrix_text(np.eye(4))
    joint_names = [f"bone_{index}" for index in range(len(skeleton.bones))]
    _name_source(skin, "joint-names", joint_names, "JOINT")
    _float_source(skin, "inverse-bind", np.asarray([matrix.T.reshape(-1) for matrix in inverse_bind]),
                  (("TRANSFORM", "float4x4"),))
    active_rows: list[list[tuple[int, float]]] = []
    for row in mesh.influences:
        merged: dict[int, float] = {}
        for influence in row:
            if influence.weight > 0.0:
                merged[influence.bone] = merged.get(influence.bone, 0.0) + influence.weight
        total = sum(merged.values()) or 1.0
        active_rows.append([(bone, weight / total) for bone, weight in merged.items()])
    flat_weights = np.asarray([weight for row in active_rows for _, weight in row], dtype=np.float32)
    _float_source(skin, "skin-weights", flat_weights.reshape(-1, 1), (("WEIGHT", "float"),))
    joints = ET.SubElement(skin, _q("joints"))
    ET.SubElement(joints, _q("input"), {"semantic": "JOINT", "source": "#joint-names"})
    ET.SubElement(joints, _q("input"), {"semantic": "INV_BIND_MATRIX", "source": "#inverse-bind"})
    vertex_weights = ET.SubElement(skin, _q("vertex_weights"), {"count": str(len(mesh.influences))})
    ET.SubElement(vertex_weights, _q("input"), {"semantic": "JOINT", "source": "#joint-names", "offset": "0"})
    ET.SubElement(vertex_weights, _q("input"), {"semantic": "WEIGHT", "source": "#skin-weights", "offset": "1"})
    ET.SubElement(vertex_weights, _q("vcount")).text = " ".join(str(len(row)) for row in active_rows)
    weight_index = 0
    values: list[str] = []
    for row in active_rows:
        for bone, _ in row:
            values.extend((str(bone), str(weight_index)))
            weight_index += 1
    ET.SubElement(vertex_weights, _q("v")).text = " ".join(values)


def _animation_libraries(root: ET.Element, skeleton: Skeleton,
                         animation_sets: Sequence[AnimationSet]) -> None:
    library = ET.SubElement(root, _q("library_animations"))
    clips_library = ET.SubElement(root, _q("library_animation_clips"))
    clip_index = 0
    for animation, name, start_ms, end_ms in animation_clips_in_export_order(
        list(animation_sets),
    ):
        group_id = f"clip-{clip_index}"
        group = ET.SubElement(library, _q("animation"), {"id": group_id, "name": name})
        for bone_index, track in enumerate(animation.tracks):
            times = sorted({start_ms, end_ms, *(key.time_ms for key in track if start_ms < key.time_ms < end_ms)})
            matrices: list[np.ndarray] = []
            parent = skeleton.parents[bone_index]
            for time_ms in times:
                child = _target_matrix(_evaluate(track, time_ms))
                if parent is not None:
                    parent_matrix = _target_matrix(_evaluate(animation.tracks[parent], time_ms))
                    child = np.linalg.inv(parent_matrix) @ child
                matrices.append(child)
            animation_id = f"{group_id}-bone-{bone_index}"
            element = ET.SubElement(group, _q("animation"), {"id": animation_id})
            time_values = np.asarray([(value - start_ms) / 1000.0 for value in times], dtype=np.float32).reshape(-1, 1)
            _float_source(element, f"{animation_id}-input", time_values, (("TIME", "float"),))
            output_values = np.asarray([matrix.T.reshape(-1) for matrix in matrices], dtype=np.float32)
            _float_source(element, f"{animation_id}-output", output_values, (("TRANSFORM", "float4x4"),))
            _name_source(element, f"{animation_id}-interpolation", ["LINEAR"] * len(times), "INTERPOLATION")
            sampler = ET.SubElement(element, _q("sampler"), {"id": f"{animation_id}-sampler"})
            ET.SubElement(sampler, _q("input"), {"semantic": "INPUT", "source": f"#{animation_id}-input"})
            ET.SubElement(sampler, _q("input"), {"semantic": "OUTPUT", "source": f"#{animation_id}-output"})
            ET.SubElement(sampler, _q("input"), {"semantic": "INTERPOLATION", "source": f"#{animation_id}-interpolation"})
            ET.SubElement(element, _q("channel"), {"source": f"#{animation_id}-sampler", "target": f"bone_{bone_index}/transform"})
        clip = ET.SubElement(clips_library, _q("animation_clip"), {
            "id": f"animation-clip-{clip_index}", "name": name,
            "start": "0", "end": format((end_ms - start_ms) / 1000.0, ".9g"),
        })
        ET.SubElement(clip, _q("instance_animation"), {"url": f"#{group_id}"})
        clip_index += 1


def _bone_node(parent: ET.Element, index: int, skeleton: Skeleton,
               bind_globals: Sequence[np.ndarray]) -> None:
    bone = skeleton.bones[index]
    node = ET.SubElement(parent, _q("node"), {
        "id": f"bone_{index}", "sid": f"bone_{index}", "name": bone.name, "type": "JOINT",
    })
    parent_index = skeleton.parents[index]
    local = bind_globals[index] if parent_index is None else np.linalg.inv(bind_globals[parent_index]) @ bind_globals[index]
    ET.SubElement(node, _q("matrix"), {"sid": "transform"}).text = _matrix_text(local)
    for child in bone.children:
        _bone_node(node, child, skeleton, bind_globals)


def _rotation_between(source: np.ndarray, target: np.ndarray) -> np.ndarray:
    """Return a stable 3D rotation taking one direction onto another."""
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    source_length = float(np.linalg.norm(source))
    target_length = float(np.linalg.norm(target))
    if source_length < 1e-9 or target_length < 1e-9:
        return np.eye(3, dtype=np.float64)
    source /= source_length
    target /= target_length
    cross = np.cross(source, target)
    sine = float(np.linalg.norm(cross))
    cosine = float(np.clip(np.dot(source, target), -1.0, 1.0))
    if sine < 1e-9:
        if cosine > 0.0:
            return np.eye(3, dtype=np.float64)
        fallback = np.asarray((1.0, 0.0, 0.0))
        if abs(float(np.dot(source, fallback))) > 0.9:
            fallback = np.asarray((0.0, 1.0, 0.0))
        axis = np.cross(source, fallback)
        axis /= np.linalg.norm(axis)
        return -np.eye(3, dtype=np.float64) + 2.0 * np.outer(axis, axis)
    axis = cross / sine
    skew = np.asarray((
        (0.0, -axis[2], axis[1]),
        (axis[2], 0.0, -axis[0]),
        (-axis[1], axis[0], 0.0),
    ))
    return np.eye(3) + skew * sine + (skew @ skew) * (1.0 - cosine)


def _human_t_pose_globals(
    skeleton: Skeleton,
    bind_globals: Sequence[np.ndarray],
) -> list[np.ndarray]:
    """Straighten a recognized biped's arms without changing its source rig.

    AM1 stores bone-local skinned vertices and AM2 stores model-global bone
    matrices.  Rebinding geometry and inverse-bind matrices to these generated
    globals makes the COLLADA rest pose a true T-pose while every source
    animation still evaluates exactly as authored.
    """
    pose = [np.asarray(matrix, dtype=np.float64).copy() for matrix in bind_globals]
    names = {bone.name.casefold(): index for index, bone in enumerate(skeleton.bones)}
    for side, direction in (("l", 1.0), ("r", -1.0)):
        chain_names = (
            f"bip01 {side} upperarm",
            f"bip01 {side} forearm",
            f"bip01 {side} hand",
            f"bip01 {side} finger0",
        )
        if any(name not in names for name in chain_names):
            return [np.asarray(matrix, dtype=np.float64).copy() for matrix in bind_globals]
        chain = [names[name] for name in chain_names]
        original = [np.asarray(bind_globals[index], dtype=np.float64) for index in chain]
        points = [matrix[:3, 3] for matrix in original]
        desired_direction = np.asarray((0.0, direction, 0.0), dtype=np.float64)
        desired = [points[0].copy()]
        for start, end in zip(points, points[1:]):
            desired.append(desired[-1] + desired_direction * np.linalg.norm(end - start))
        rotations: list[np.ndarray] = []
        for position, next_position, target_position, target_next in zip(
            points, points[1:], desired, desired[1:],
        ):
            rotation = _rotation_between(next_position - position, target_next - target_position)
            rotations.append(rotation)
        rotations.append(rotations[-1])
        for index, matrix, position, target_position, rotation in zip(
            chain, original, points, desired, rotations,
        ):
            transformed = matrix.copy()
            transformed[:3, :3] = rotation @ matrix[:3, :3]
            transformed[:3, 3] = target_position
            pose[index] = transformed
    return pose


def _scene(root: ET.Element, mesh: Am1Mesh, skeleton: Skeleton,
           bind_globals: Sequence[np.ndarray], material_count: int) -> None:
    library = ET.SubElement(root, _q("library_visual_scenes"))
    scene = ET.SubElement(library, _q("visual_scene"), {"id": "Scene", "name": "Scene"})
    for index, parent in enumerate(skeleton.parents):
        if parent is None:
            _bone_node(scene, index, skeleton, bind_globals)
    model = ET.SubElement(scene, _q("node"), {"id": "mpe-model", "name": "Marine Park Empire model"})
    instance = ET.SubElement(model, _q("instance_controller"), {"url": "#mpe-skin"})
    for index, parent in enumerate(skeleton.parents):
        if parent is None:
            ET.SubElement(instance, _q("skeleton")).text = f"#bone_{index}"
    bind = ET.SubElement(instance, _q("bind_material"))
    common = ET.SubElement(bind, _q("technique_common"))
    for slot in range(material_count):
        material = ET.SubElement(common, _q("instance_material"), {
            "symbol": f"material-symbol-{slot}", "target": f"#material-{slot}",
        })
        ET.SubElement(material, _q("bind_vertex_input"), {
            "semantic": "UVSET0", "input_semantic": "TEXCOORD", "input_set": "0",
        })
    document_scene = ET.SubElement(root, _q("scene"))
    ET.SubElement(document_scene, _q("instance_visual_scene"), {"url": "#Scene"})


def write_animated_dae(mesh: Am1Mesh, skeleton: Skeleton,
                       animation: AnimationSet | Sequence[AnimationSet],
                       output: Path, *, texture_filenames: Sequence[str | None] = (),
                       human_t_pose: bool = False) -> Path:
    """Write geometry, controller, skeleton, inverse binds, and all AM2 clips."""
    animation_sets = [animation] if isinstance(animation, AnimationSet) else list(animation)
    if (not animation_sets or mesh.bone_count != len(skeleton.bones) or
            any(len(item.tracks) != len(skeleton.bones) for item in animation_sets)):
        raise ValueError("AM1, AM2, and AM3 bone counts do not match")
    source_bind = [_evaluate(track, 0.0) for track in animation_sets[0].tracks]
    if human_t_pose:
        source_bind = _human_t_pose_globals(skeleton, source_bind)
    target_bind = [_target_matrix(matrix) for matrix in source_bind]
    positions, normals, _, _ = _bind_geometry(mesh, source_bind)
    inverse_bind = np.asarray([np.linalg.inv(matrix) for matrix in target_bind])
    material_count = max(mesh.face_materials, default=0) + 1

    root = ET.Element(_q("COLLADA"), {"version": "1.4.1"})
    asset = ET.SubElement(root, _q("asset"))
    contributor = ET.SubElement(asset, _q("contributor"))
    ET.SubElement(contributor, _q("authoring_tool")).text = "RAE Windows ISO"
    ET.SubElement(asset, _q("unit"), {"name": "centimeter", "meter": "0.01"})
    ET.SubElement(asset, _q("up_axis")).text = "Y_UP"
    _materials(root, texture_filenames, material_count)
    _geometry(root, mesh, positions, normals)
    _controller(root, mesh, skeleton, inverse_bind)
    _animation_libraries(root, skeleton, animation_sets)
    _scene(root, mesh, skeleton, target_bind, material_count)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
    return output
