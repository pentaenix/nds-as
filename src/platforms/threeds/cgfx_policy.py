"""Conservative GLB material policy for generic NintendoWare CGFX assets."""
from __future__ import annotations

import io
from pathlib import Path
import re
from xml.etree import ElementTree as ET

from PIL import Image

from .gltf.glb_io import material_texture_bytes, read_glb


def _is_lbx_outline(name: object) -> bool:
    return str(name or "").casefold().startswith("line_m")


def apply_cgfx_dae_policy(
    path: Path,
    *,
    game_id: str,
    material_states: object = None,
) -> None:
    """Preserve LBX outline and authored blend intent in portable COLLADA."""
    if game_id != "lbx":
        return
    tree = ET.parse(path)
    root = tree.getroot()
    namespace = root.tag.partition("}")[0].removeprefix("{") if "}" in root.tag else ""
    tag = lambda name: f"{{{namespace}}}{name}" if namespace else name
    states = _material_state_map(material_states)
    effect_materials: dict[str, str] = {}
    for material in root.findall(f".//{tag('material')}"):
        name = str(material.get("name") or material.get("id") or "")
        for instance in material.findall(tag("instance_effect")):
            effect_materials[str(instance.get("url") or "").removeprefix("#")] = name
    changed = False
    for effect in root.findall(f".//{tag('effect')}"):
        material_name = effect_materials.get(str(effect.get("id") or ""), "")
        if _is_lbx_outline(material_name):
            for diffuse in effect.findall(f".//{tag('diffuse')}"):
                for child in list(diffuse):
                    diffuse.remove(child)
                color = ET.SubElement(diffuse, tag("color"))
                color.text = "0.04 0.04 0.04 1"
                changed = True
        state = _source_state_for(material_name, states)
        if state is None or _source_render_state(state)[0] != "BLEND":
            continue
        shader = next(
            (effect.find(f".//{tag(kind)}") for kind in ("phong", "blinn", "lambert")),
            None,
        )
        if shader is None or shader.find(tag("transparent")) is not None:
            continue
        transparent = ET.SubElement(shader, tag("transparent"), {"opaque": "A_ONE"})
        ET.SubElement(transparent, tag("color")).text = "1 1 1 1"
        ET.SubElement(ET.SubElement(shader, tag("transparency")), tag("float")).text = "1"
        changed = True
    if changed:
        if namespace:
            ET.register_namespace("", namespace)
        tree.write(path, encoding="utf-8", xml_declaration=True)


def _alpha_mode(texture: bytes | None) -> tuple[str, float | None]:
    if not texture:
        return "OPAQUE", None
    try:
        with Image.open(io.BytesIO(texture)) as image:
            if "A" not in image.getbands():
                return "OPAQUE", None
            values = image.getchannel("A").getextrema()
            if not values or values[0] == 255:
                return "OPAQUE", None
            colors = image.getchannel("A").getcolors(maxcolors=257)
            if colors is not None and all(alpha in {0, 255} for _count, alpha in colors):
                return "MASK", 0.5
            return "BLEND", None
    except Exception:
        return "OPAQUE", None


def _enum_name(value: object) -> str:
    text = re.sub(r"(?<!^)(?=[A-Z])", "_", str(value or ""))
    return text.replace(" ", "_").casefold()


def _material_state_map(material_states: object) -> dict[str, dict]:
    if not isinstance(material_states, list):
        return {}
    mapped: dict[str, dict] = {}
    for state in material_states:
        if not isinstance(state, dict):
            continue
        name = str(state.get("name") or "").casefold()
        if name:
            mapped[name] = state
    return mapped


def _source_state_for(name: str, states: dict[str, dict]) -> dict | None:
    key = name.casefold()
    if key in states:
        return states[key]
    stripped = key.removesuffix("_id").removesuffix("-material")
    if stripped in states:
        return states[stripped]
    return next(
        (state for source_name, state in states.items() if source_name in key or stripped in source_name),
        None,
    )


def _source_render_state(state: dict) -> tuple[str, float | None, dict]:
    alpha_test = bool(state.get("alphaTestEnabled"))
    source = _enum_name(state.get("colorSourceFactor"))
    destination = _enum_name(state.get("colorDestinationFactor"))
    blended = (source, destination) != ("one", "zero")
    if alpha_test:
        mode = "MASK"
        cutoff = max(0.0, min(1.0, float(state.get("alphaTestReference") or 0) / 255.0))
    elif blended or str(state.get("renderingPreset") or "").casefold() == "translucent":
        mode, cutoff = "BLEND", None
    else:
        mode, cutoff = "OPAQUE", None
    pica = {
        "authoritative": True,
        "faceCulling": _enum_name(state.get("faceCulling")),
        "renderLayer": int(state.get("renderLayer") or 0),
        "alphaTestEnabled": alpha_test,
        "alphaTestFunction": _enum_name(state.get("alphaTestFunction")),
        "alphaTestReference": max(
            0.0, min(1.0, float(state.get("alphaTestReference") or 0) / 255.0)
        ),
        "alphaBlendEnabled": blended,
        "sourceRgbFactor": source,
        "destinationRgbFactor": destination,
        "sourceAlphaFactor": _enum_name(state.get("alphaSourceFactor")),
        "destinationAlphaFactor": _enum_name(state.get("alphaDestinationFactor")),
        "depthTestEnabled": bool(state.get("depthTestEnabled", True)),
        "depthWriteEnabled": bool(
            state.get("depthWriteEnabled", state.get("depthBufferWrite", True))
        ),
    }
    return mode, cutoff, pica


def apply_cgfx_glb_policy(
    path: Path,
    *,
    game_id: str,
    animation_name: str | None = None,
    material_states: object = None,
) -> None:
    glb = read_glb(path)
    source_states = _material_state_map(material_states)
    root_rae = glb.json.setdefault("extras", {}).setdefault("rae", {})
    root_rae.update({"platform": "3ds", "schemaVersion": 1, "game": game_id, "format": "cgfx"})
    if animation_name:
        animations = glb.json.get("animations") or []
        if animations and isinstance(animations[0], dict):
            # Assimp derives a Collada animation name from the first animated
            # channel.  Restore the authored CGFX clip name shown in RAE.
            animations[0]["name"] = animation_name
    for index, material in enumerate(glb.json.get("materials") or []):
        if not isinstance(material, dict):
            continue
        state = _source_state_for(str(material.get("name") or ""), source_states)
        if state is not None:
            mode, cutoff, pica = _source_render_state(state)
        else:
            mode, cutoff = _alpha_mode(material_texture_bytes(glb, index))
            pica = None
        material["alphaMode"] = mode
        if cutoff is None:
            material.pop("alphaCutoff", None)
        else:
            material["alphaCutoff"] = cutoff
        face_culling = str((pica or {}).get("faceCulling") or "")
        material["doubleSided"] = face_culling == "never" if pica else True
        rae = material.setdefault("extras", {}).setdefault("rae", {})
        render_class = mode.lower()
        if pica and (
            pica.get("sourceRgbFactor") == "source_alpha"
            and pica.get("destinationRgbFactor") == "one"
        ):
            render_class = "additive"
        rae.update({"platform": "3ds", "schemaVersion": 1, "renderClass": render_class})
        if pica:
            rae["pica"] = pica
        if game_id == "lbx" and _is_lbx_outline(material.get("name")):
            pbr = material.setdefault("pbrMetallicRoughness", {})
            pbr.pop("baseColorTexture", None)
            pbr["baseColorFactor"] = [0.04, 0.04, 0.04, 1.0]
            material["alphaMode"] = "OPAQUE"
            material.pop("alphaCutoff", None)
            rae["renderClass"] = "opaque"
            rae["lbxOutline"] = True
    glb.write(path)
