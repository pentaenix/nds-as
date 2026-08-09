#!/usr/bin/env python3
"""Replace a trivial static COLLADA skin with a direct geometry instance."""
from __future__ import annotations

import argparse
import copy
import math
import shutil
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

_NS = "http://www.collada.org/2005/11/COLLADASchema"
_PREFIX = f"{{{_NS}}}"


def _tag(name: str) -> str:
    return f"{_PREFIX}{name}"


def _floats(text: str | None) -> list[float]:
    return [float(value) for value in (text or "").split()]


def _is_identity(values: list[float]) -> bool:
    identity = [
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        0.0, 0.0, 0.0, 1.0,
    ]
    return len(values) == 16 and all(
        math.isclose(actual, expected, abs_tol=1e-7)
        for actual, expected in zip(values, identity, strict=True)
    )


def _parent_of(root: ET.Element, child: ET.Element) -> ET.Element:
    for node in root.iter():
        if child in list(node):
            return node
    raise ValueError("element has no parent")


def flatten_static_skin(source: Path, output: Path) -> dict[str, int | str]:
    """Write a direct-geometry DAE after proving its skin is a no-op."""
    source = Path(source)
    output = Path(output)
    tree = ET.parse(source)
    root = tree.getroot()

    animations = root.findall(f".//{_tag('library_animations')}/{_tag('animation')}")
    clips = root.findall(f".//{_tag('library_animation_clips')}/{_tag('animation_clip')}")
    if animations or clips:
        raise ValueError("refusing to flatten a DAE containing animation data")

    controller_libraries = root.findall(_tag("library_controllers"))
    controllers = root.findall(f".//{_tag('library_controllers')}/{_tag('controller')}")
    instances = root.findall(f".//{_tag('instance_controller')}")
    if len(controller_libraries) != 1 or len(controllers) != 1 or len(instances) != 1:
        raise ValueError("expected exactly one skin controller and one instance")

    controller = controllers[0]
    instance = instances[0]
    if instance.get("url") != f"#{controller.get('id')}":
        raise ValueError("controller instance does not target the only controller")
    skin = controller.find(_tag("skin"))
    if skin is None or not skin.get("source", "").startswith("#"):
        raise ValueError("controller does not contain a local skin source")
    geometry_id = skin.get("source", "")[1:]
    if root.find(f".//{_tag('geometry')}[@id='{geometry_id}']") is None:
        raise ValueError("skin source geometry is missing")

    bind_shape = skin.find(_tag("bind_shape_matrix"))
    if bind_shape is not None and not _is_identity(_floats(bind_shape.text)):
        raise ValueError("refusing to flatten a non-identity bind-shape matrix")

    joint_array = skin.find(f".//{_tag('Name_array')}")
    weight_source = next(
        (
            node
            for node in skin.findall(_tag("source"))
            if node.get("id", "").endswith("-weights")
        ),
        None,
    )
    weight_array = weight_source.find(_tag("float_array")) if weight_source is not None else None
    vertex_weights = skin.find(_tag("vertex_weights"))
    if joint_array is None or weight_array is None or vertex_weights is None:
        raise ValueError("skin joint or weight data is incomplete")
    joints = (joint_array.text or "").split()
    weights = _floats(weight_array.text)
    counts = [int(value) for value in (vertex_weights.findtext(_tag("vcount")) or "").split()]
    assignments = [int(value) for value in (vertex_weights.findtext(_tag("v")) or "").split()]
    if len(joints) != 1 or len(weights) != 1 or not math.isclose(weights[0], 1.0, abs_tol=1e-7):
        raise ValueError("skin is not a single identity-weight joint")
    if not counts or any(count != 1 for count in counts):
        raise ValueError("one or more vertices have non-trivial skin influences")
    if len(assignments) != len(counts) * 2 or any(
        value != 0 for value in assignments
    ):
        raise ValueError("one or more vertices target a non-identity joint or weight")

    direct = ET.Element(_tag("instance_geometry"), {"url": f"#{geometry_id}"})
    bind_material = instance.find(_tag("bind_material"))
    if bind_material is not None:
        direct.append(copy.deepcopy(bind_material))
    instance_parent = _parent_of(root, instance)
    instance_index = list(instance_parent).index(instance)
    instance_parent.remove(instance)
    instance_parent.insert(instance_index, direct)

    skeleton_ids = {
        (node.text or "").strip().removeprefix("#")
        for node in instance.findall(_tag("skeleton"))
        if (node.text or "").strip()
    }
    for node in list(root.findall(f".//{_tag('node')}")):
        if node.get("id") not in skeleton_ids:
            continue
        if node.get("type") != "JOINT" or list(node.findall(_tag("node"))):
            raise ValueError("refusing to remove a non-trivial skeleton node")
        _parent_of(root, node).remove(node)
    root.remove(controller_libraries[0])

    ET.register_namespace("", _NS)
    ET.indent(tree, space="  ")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        tree.write(temporary, encoding="utf-8", xml_declaration=True)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "geometry": geometry_id,
        "vertices": len(counts),
        "removedJoints": len(skeleton_ids),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backup", type=Path)
    args = parser.parse_args()
    output = args.output or args.source
    if output.resolve() == args.source.resolve():
        if args.backup is None:
            parser.error("--backup is required when replacing the source DAE")
        args.backup.parent.mkdir(parents=True, exist_ok=True)
        if args.backup.exists():
            raise FileExistsError(f"backup already exists: {args.backup}")
        shutil.copy2(args.source, args.backup)
    result = flatten_static_skin(args.source, output)
    print(
        f"Flattened {result['geometry']}: {result['vertices']} vertices; "
        f"removed {result['removedJoints']} identity joint(s)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
