"""Decode Nitro BTA0 texture-translation tracks and attach preview metadata."""
from __future__ import annotations

import base64
import io
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .gltf.glb_io import embedded_image_bytes, read_glb

# NSBTA stores sample indices and frame counts but no FPS field. Gen 5's
# overworld runs at 30 Hz, but ambient material tracks advance one stored
# sample every two overworld ticks. Keep the 30 Hz source clock in metadata and
# play the decoded samples at 15 Hz; tying samples one-for-one to display frames
# made RAE and Resort run water twice as fast on a 60 Hz display.
NITRO_SOURCE_FRAME_RATE = 30
RESORT_MAP_FRAME_RATE = 15
NITRO_PATTERN_FRAME_RATE = 15
_BOUNDED_EDGE_MOTION_SCALE = {
    # This 16x16 wave crest is a rock/water seam, not a scrolling water plane.
    # Its raw 19-pixel SRT excursion makes the isolated GLB cycle the complete
    # tile before returning.  Gen 5's intended edge read is the local 1/8 phase.
    "sea_gake02": 1.0 / 8.0,
}
_MATERIAL_MOTION_EXCURSION_SCALE = {
    # ``sea_zanami`` is the beach crest. Its decoded V channel travels
    # 0.2602539 of a 64 px texture (16.65625 px), which withdraws essentially
    # the complete authored wave strip. Gen 5 keeps roughly half of that strip
    # at maximum retreat. Preserve the ROM horizontal drift and maximum
    # shoreline advance, but pull withdrawn samples halfway toward that upper
    # extreme (8.328125 px of travel).
    "sea_zanami": (1.0, 0.5),
}
_MATERIAL_MOTION_EXCURSION_ANCHOR = {
    # Frame zero is the withdrawn pose, not the fully advanced crest. Scaling
    # around it prevents the wave from reaching the top of the beach. Keep the
    # V maximum invariant instead.
    "sea_zanami": ("first", "max"),
}
_MATERIAL_FRAME_RATES = {
    # The bounded rock/water seam was already visually correct at 20 fps.
    "sea_gake02": 20,
}
_MATERIAL_FRAME_RATE_PREFIXES = {
    # Long 240-frame fountain and waterfall streams became effectively static
    # at the earlier slowed map-water rate. These tracks are explicitly known
    # to advance every source tick, unlike 15 Hz ambient shoreline motion.
    "c07_foun": 30,
    "kawa01": 30,
}


@dataclass(frozen=True)
class MaterialMotionTrack:
    material: str
    frame_offsets: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class MaterialMotionClip:
    name: str
    frame_count: int
    tracks: tuple[MaterialMotionTrack, ...]


@dataclass(frozen=True)
class PatternKeyframe:
    frame: int
    texture_index: int
    palette_index: int


@dataclass(frozen=True)
class PatternTrack:
    frame_count: int
    keyframes: tuple[PatternKeyframe, ...]
    texture_data: bytes


@dataclass(frozen=True)
class NitroPatternMaterialTrack:
    material: str
    keyframes: tuple[PatternKeyframe, ...]


@dataclass(frozen=True)
class NitroPatternClip:
    name: str
    frame_count: int
    texture_names: tuple[str, ...]
    palette_names: tuple[str, ...]
    tracks: tuple[NitroPatternMaterialTrack, ...]


def _name(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("shift_jis", errors="replace").strip()


def _info_block(data: bytes, offset: int, datum_size: int) -> tuple[list[bytes], list[str]]:
    if offset < 0 or offset + 16 > len(data) or data[offset] != 0:
        raise ValueError("Invalid Nitro info block")
    count = data[offset + 1]
    table = offset + 12 + count * 4
    if table + 4 > len(data):
        raise ValueError("Truncated Nitro info block")
    actual_size = struct.unpack_from("<H", data, table)[0]
    if actual_size != datum_size:
        raise ValueError(f"Unexpected Nitro info datum size {actual_size}; expected {datum_size}")
    values_start = table + 4
    values_end = values_start + count * datum_size
    names_end = values_end + count * 16
    if names_end > len(data):
        raise ValueError("Truncated Nitro info values/names")
    values = [
        bytes(data[values_start + index * datum_size : values_start + (index + 1) * datum_size])
        for index in range(count)
    ]
    names = [_name(data[values_end + index * 16 : values_end + (index + 1) * 16]) for index in range(count)]
    return values, names


def _fixed_s10_5(value: int) -> float:
    """Decode Nitro's signed 10.5 texture translation in texels."""
    signed = value if value < 0x8000 else value - 0x10000
    return signed / 32.0


def _fixed_s20_12(value: int) -> float:
    """Decode Nitro's signed 20.12 texture translation in texels."""
    signed = value if value < 0x80000000 else value - 0x100000000
    return signed / 4096.0


def _translation_samples(data: bytes, animation_start: int, channel: bytes) -> list[float]:
    frame_count, _dummy, flags, value_or_offset = struct.unpack("<HBBI", channel)
    if frame_count <= 0:
        return []
    # Bit 0x20 marks a constant channel. Bit 0x10 selects compact signed 10.5
    # samples; without it, Nitro stores full signed 20.12 samples. Waterfall
    # bodies (`kawa01*`) use the wide form while ordinary water often uses the
    # compact form, so treating unknown/wide channels as zero freezes falls.
    if flags & 0x20:
        return [_fixed_s20_12(value_or_offset)] * frame_count
    start = animation_start + value_or_offset
    sample_size = 2 if flags & 0x10 else 4
    end = start + frame_count * sample_size
    if start < 0 or end > len(data):
        return []
    if flags & 0x10:
        return [_fixed_s10_5(value) for value in struct.unpack_from(f"<{frame_count}H", data, start)]
    return [value / 4096.0 for value in struct.unpack_from(f"<{frame_count}i", data, start)]


def _stable_offset(value: float) -> float:
    """Preserve signed Nitro translation and mirrored-repeat parity."""
    return round(value, 7)


def _normalized_material_offsets(
    track: MaterialMotionTrack,
    *,
    material_key: str,
    width: int,
    height: int,
) -> list[list[float]]:
    """Normalize ROM texel offsets while retaining each material's baseline."""
    bounded_scale = _BOUNDED_EDGE_MOTION_SCALE.get(material_key, 1.0)
    excursion_scale = _MATERIAL_MOTION_EXCURSION_SCALE.get(material_key)
    if excursion_scale is None or not track.frame_offsets:
        return [
            [
                _stable_offset((u / width) * bounded_scale),
                _stable_offset((v / height) * bounded_scale),
            ]
            for u, v in track.frame_offsets
        ]

    base_u, base_v = track.frame_offsets[0]
    anchor_u, anchor_v = _MATERIAL_MOTION_EXCURSION_ANCHOR.get(
        material_key,
        ("first", "first"),
    )
    if anchor_u == "max":
        base_u = max(offset[0] for offset in track.frame_offsets)
    elif anchor_u == "min":
        base_u = min(offset[0] for offset in track.frame_offsets)
    if anchor_v == "max":
        base_v = max(offset[1] for offset in track.frame_offsets)
    elif anchor_v == "min":
        base_v = min(offset[1] for offset in track.frame_offsets)
    scale_u, scale_v = excursion_scale
    return [
        [
            _stable_offset((base_u + (u - base_u) * scale_u) / width),
            _stable_offset((base_v + (v - base_v) * scale_v) / height),
        ]
        for u, v in track.frame_offsets
    ]


def normalize_gen5_shoreline_motion(tracks: list[dict]) -> int:
    """Compatibility no-op: Gen 5 shoreline tracks must remain ROM-authored.

    ``sea_zanami`` and ``sea_zanami2`` are independent layers with different
    horizontal phases; only the former has a bounded vertical excursion.
    Rewriting either track changes both the visible travel and their relative
    counter-motion, so preview and export now preserve the decoded samples.
    """
    del tracks
    return 0


def _animation_material_matches(
    material_names: dict[str, list[tuple[int, str]]],
    track_name: str,
    exact_track_names: set[str],
) -> list[tuple[int, str]]:
    """Resolve apicula's duplicate-name suffixes without stealing exact tracks."""
    key = str(track_name or "").casefold()
    exact = material_names.get(key) or []
    if exact:
        return [exact[0]]
    candidates: list[tuple[int, str]] = []
    for material_key, matches in material_names.items():
        if not re.fullmatch(re.escape(key) + r"(?:[._-]\d+)", material_key):
            continue
        # When BTA contains both foo and foo_2, foo_2 belongs to its own exact
        # track.  The unsuffixed foo track should bind apicula's remaining foo_1.
        if material_key in exact_track_names:
            continue
        candidates.extend(matches)
    unique = list({exact_name.casefold(): (index, exact_name) for index, exact_name in candidates}.values())
    return unique if len(unique) == 1 else []


def _append_float_accessor(
    glb,
    values: list[tuple[float, ...]],
    value_type: str,
) -> int:
    width = {"SCALAR": 1, "VEC2": 2, "VEC3": 3}[value_type]
    binary = bytearray(glb.bin_chunk)
    while len(binary) % 4:
        binary.append(0)
    start = len(binary)
    packer = struct.Struct("<" + "f" * width)
    for value in values:
        binary.extend(packer.pack(*(float(item) for item in value)))
    views = glb.json.setdefault("bufferViews", [])
    view_index = len(views)
    views.append({"buffer": 0, "byteOffset": start, "byteLength": len(binary) - start})
    accessor: dict = {
        "bufferView": view_index,
        "componentType": 5126,
        "count": len(values),
        "type": value_type,
    }
    if values:
        accessor["min"] = [min(row[axis] for row in values) for axis in range(width)]
        accessor["max"] = [max(row[axis] for row in values) for axis in range(width)]
    accessors = glb.json.setdefault("accessors", [])
    accessor_index = len(accessors)
    accessors.append(accessor)
    glb.bin_chunk = bytes(binary)
    buffers = glb.json.setdefault("buffers", [{"byteLength": 0}])
    if not buffers:
        buffers.append({"byteLength": len(binary)})
    else:
        buffers[0]["byteLength"] = len(binary)
    return accessor_index


def sync_material_motion_property_animations(glb) -> int:
    """Mirror RAE UV tracks into apicula's portable GLB animation extension.

    Three.js uses ``extras.rae.mapMaterialMotion`` for live preview.  Exported
    GLBs also carry ``EXT_property_animation`` so a consumer that understands
    apicula's extension can play the same material motion without the ROM.
    """
    animations = [
        animation
        for animation in (glb.json.get("animations") or [])
        if str((((animation.get("extras") or {}).get("rae") or {}).get("source") or ""))
        != "mapMaterialMotion"
        and not (
            isinstance((animation.get("extensions") or {}).get("EXT_property_animation"), dict)
            # apicula's experimental BTA export represents the same tracks;
            # replace it so the GLB and RAE metadata cannot disagree.
            and not (animation.get("extras") or {}).get("rae")
        )
    ]
    motion = (((glb.json.get("extras") or {}).get("rae") or {}).get("mapMaterialMotion") or {})
    clips = [clip for clip in (motion.get("clips") or []) if isinstance(clip, dict)]
    if not clips:
        if animations:
            glb.json["animations"] = animations
        else:
            glb.json.pop("animations", None)
        return 0

    materials = glb.json.get("materials") or []
    material_indices: dict[str, list[int]] = {}
    for index, material in enumerate(materials):
        if isinstance(material, dict) and material.get("name"):
            material_indices.setdefault(str(material["name"]).casefold(), []).append(index)
    fps = max(1.0, float(motion.get("frameRate") or RESORT_MAP_FRAME_RATE))
    generated = 0
    for clip in clips:
        property_channels: list[dict] = []
        samplers: list[dict] = []
        for track in clip.get("tracks") or []:
            if not isinstance(track, dict) or not track.get("frameOffsets"):
                continue
            matching_indices = material_indices.get(str(track.get("material") or "").casefold()) or []
            if not matching_indices:
                continue
            offsets = [
                (float(value[0]), float(value[1]))
                for value in track.get("frameOffsets") or []
                if isinstance(value, (list, tuple)) and len(value) >= 2
            ]
            if len(offsets) < 2:
                continue
            track_fps = max(1.0, float(track.get("frameRate") or fps))
            for material_index in matching_indices:
                try:
                    texture_info = materials[material_index]["pbrMetallicRoughness"]["baseColorTexture"]
                except (KeyError, TypeError):
                    continue
                texture_info.setdefault("extensions", {}).setdefault(
                    "KHR_texture_transform", {"offset": [0.0, 0.0]}
                )
                timeline = [(index / track_fps,) for index in range(len(offsets))]
                input_index = _append_float_accessor(glb, timeline, "SCALAR")
                output_index = _append_float_accessor(glb, offsets, "VEC2")
                sampler_index = len(samplers)
                samplers.append({"input": input_index, "output": output_index, "interpolation": "STEP"})
                property_channels.append(
                    {
                        "target": (
                            f"/materials/{material_index}/pbrMetallicRoughness/baseColorTexture/"
                            "extensions/KHR_texture_transform/offset"
                        ),
                        "sampler": sampler_index,
                    }
                )
        if not samplers:
            continue
        animations.append(
            {
                "name": str(clip.get("name") or clip.get("id") or f"material_motion_{generated}"),
                "samplers": samplers,
                # EXT_property_animation owns the real UV targets.  This inert
                # channel is the compatibility shape emitted by apicula itself.
                "channels": [{"target": {"path": "scale"}, "sampler": 0}],
                "extensions": {"EXT_property_animation": {"channels": property_channels}},
                "extras": {"rae": {"source": "mapMaterialMotion", "loop": clip.get("loop") is not False}},
            }
        )
        generated += 1
    if animations:
        glb.json["animations"] = animations
    for extension in ("KHR_texture_transform", "EXT_property_animation"):
        used = glb.json.setdefault("extensionsUsed", [])
        if generated and extension not in used:
            used.append(extension)
    return generated


def parse_bta0_material_motion(data: bytes) -> tuple[MaterialMotionClip, ...]:
    if len(data) < 20 or data[:4] != b"BTA0":
        raise ValueError("Not a BTA0 material-animation file")
    section_count = struct.unpack_from("<H", data, 14)[0]
    section_offsets = [
        struct.unpack_from("<I", data, 16 + index * 4)[0]
        for index in range(section_count)
        if 16 + index * 4 + 4 <= len(data)
    ]
    clips: list[MaterialMotionClip] = []
    for section_start in section_offsets:
        if section_start + 8 > len(data) or data[section_start : section_start + 4] != b"SRT0":
            continue
        animation_entries, animation_names = _info_block(data, section_start + 8, 4)
        for entry, fallback_name in zip(animation_entries, animation_names):
            animation_start = section_start + struct.unpack("<I", entry)[0]
            if animation_start + 16 > len(data):
                continue
            frame_count = struct.unpack_from("<H", data, animation_start + 4)[0]
            track_entries, track_names = _info_block(data, animation_start + 8, 40)
            tracks: list[MaterialMotionTrack] = []
            for body, material_name in zip(track_entries, track_names):
                if not material_name:
                    continue
                channels = [body[index * 8 : (index + 1) * 8] for index in range(5)]
                u_values = _translation_samples(data, animation_start, channels[3])
                v_values = _translation_samples(data, animation_start, channels[4])
                count = min(frame_count, max(len(u_values), len(v_values)))
                if count <= 0:
                    continue
                if not u_values:
                    u_values = [0.0] * count
                if not v_values:
                    v_values = [0.0] * count
                offsets = tuple(
                    (
                        u_values[min(index, len(u_values) - 1)],
                        v_values[min(index, len(v_values) - 1)],
                    )
                    for index in range(count)
                )
                if len(set(offsets)) < 2:
                    continue
                tracks.append(MaterialMotionTrack(material=material_name, frame_offsets=offsets))
            if tracks:
                clips.append(
                    MaterialMotionClip(
                        name=fallback_name or f"material_motion_{len(clips)}",
                        frame_count=frame_count,
                        tracks=tuple(tracks),
                    )
                )
    return tuple(clips)


def attach_bta0_material_motion(glb_path: Path, bta0_files: list[bytes]) -> int:
    """Write root ``extras.rae.mapMaterialMotion`` consumed by the NDS viewer."""
    glb = read_glb(glb_path)
    materials = glb.json.get("materials") or []
    material_names: dict[str, list[tuple[int, str]]] = {}
    for index, material in enumerate(materials):
        if not isinstance(material, dict) or not material.get("name"):
            continue
        exact_name = str(material["name"])
        material_names.setdefault(exact_name.casefold(), []).append((index, exact_name))

    def texture_dimensions(material_index: int) -> tuple[int, int]:
        try:
            from PIL import Image

            material = materials[material_index]
            texture_index = material["pbrMetallicRoughness"]["baseColorTexture"]["index"]
            texture = glb.json["textures"][texture_index]
            image_index = texture["source"]
            image = glb.json["images"][image_index]
            payload = embedded_image_bytes(glb, image_index)
            if payload is not None:
                with Image.open(io.BytesIO(payload)) as decoded:
                    return max(1, decoded.width), max(1, decoded.height)
            uri = str(image.get("uri") or "")
            if uri and not uri.startswith("data:"):
                with Image.open(glb_path.parent / uri) as decoded:
                    return max(1, decoded.width), max(1, decoded.height)
        except Exception:
            pass
        return 1, 1
    parsed_clips: list[MaterialMotionClip] = []
    for data in bta0_files:
        try:
            parsed_clips.extend(parse_bta0_material_motion(data))
        except (ValueError, struct.error):
            continue
    exact_track_names = {
        track.material.casefold()
        for clip in parsed_clips
        for track in clip.tracks
    }
    clips_json: list[dict] = []
    for clip in parsed_clips:
        tracks = []
        for track in clip.tracks:
            for material_match in _animation_material_matches(
                material_names,
                track.material,
                exact_track_names,
            ):
                material_index, exact_name = material_match
                width, height = texture_dimensions(material_index)
                exact_key = exact_name.casefold()
                frame_rate = _MATERIAL_FRAME_RATES.get(exact_key)
                if frame_rate is None:
                    frame_rate = next(
                        (
                            value
                            for prefix, value in _MATERIAL_FRAME_RATE_PREFIXES.items()
                            if exact_key.startswith(prefix)
                        ),
                        None,
                    )
                tracks.append(
                    {
                        "material": exact_name,
                        "frameOffsets": _normalized_material_offsets(
                            track,
                            material_key=exact_key,
                            width=width,
                            height=height,
                        ),
                        **({"frameRate": frame_rate} if frame_rate is not None else {}),
                    }
                )
        if tracks:
            normalize_gen5_shoreline_motion(tracks)
            clips_json.append(
                {
                    "id": clip.name,
                    "name": clip.name,
                    "frameCount": clip.frame_count,
                    "loop": True,
                    "tracks": tracks,
                }
            )
    if not clips_json:
        return 0
    matched_track_count = sum(len(clip["tracks"]) for clip in clips_json)
    if len(clips_json) > 1:
        # Area terrain and placed-object records carry independent ambient
        # animations, but the game runs them together.  Preserve each source
        # clip for manual inspection and add an aggregate default whose tracks
        # loop independently in the NDS viewport and Resort tile runtime.
        combined_tracks = [
            track
            for clip in clips_json
            for track in clip.get("tracks") or []
        ]
        combined = {
            "id": "exact_map_ambient",
            "name": "Exact map ambient animations",
            "frameCount": max(int(clip.get("frameCount") or 1) for clip in clips_json),
            "loop": True,
            "tracks": combined_tracks,
        }
        clips_json.insert(0, combined)
    rae = glb.json.setdefault("extras", {}).setdefault("rae", {})
    rae["mapMaterialMotion"] = {
        "frameRate": RESORT_MAP_FRAME_RATE,
        "sourceFrameRate": NITRO_SOURCE_FRAME_RATE,
        "defaultClip": clips_json[0]["id"],
        "clips": clips_json,
    }
    sync_material_motion_property_animations(glb)
    glb.write(glb_path)
    return matched_track_count


def parse_btp0_pattern_motion(data: bytes) -> tuple[NitroPatternClip, ...]:
    """Decode standard Nitro PAT0 texture/palette keyframes from an NSBTP."""
    if len(data) < 20 or data[:4] != b"BTP0":
        raise ValueError("Not a BTP0 texture-pattern animation file")
    section_count = struct.unpack_from("<H", data, 14)[0]
    clips: list[NitroPatternClip] = []
    for section_index in range(section_count):
        table_offset = 16 + section_index * 4
        if table_offset + 4 > len(data):
            continue
        section_start = struct.unpack_from("<I", data, table_offset)[0]
        if section_start + 8 > len(data) or data[section_start : section_start + 4] != b"PAT0":
            continue
        entries, names = _info_block(data, section_start + 8, 4)
        for entry, fallback_name in zip(entries, names):
            animation_start = section_start + struct.unpack("<I", entry)[0]
            if animation_start + 12 > len(data):
                continue
            frame_count, texture_count, palette_count, texture_offset, palette_offset = struct.unpack_from(
                "<HBBHH", data, animation_start + 4
            )

            def names_at(relative: int, count: int) -> tuple[str, ...]:
                start = animation_start + relative
                end = start + count * 16
                if start < animation_start or end > len(data):
                    return ()
                return tuple(
                    _name(data[start + index * 16 : start + (index + 1) * 16])
                    for index in range(count)
                )

            texture_names = names_at(texture_offset, texture_count)
            palette_names = names_at(palette_offset, palette_count)
            if len(texture_names) != texture_count or len(palette_names) != palette_count:
                continue
            track_entries, track_names = _info_block(data, animation_start + 12, 8)
            tracks: list[NitroPatternMaterialTrack] = []
            for body, material in zip(track_entries, track_names):
                keyframe_count, _unknown, relative = struct.unpack("<IHH", body)
                start = animation_start + relative
                end = start + keyframe_count * 4
                if not material or keyframe_count <= 0 or start < animation_start or end > len(data):
                    continue
                keyframes: list[PatternKeyframe] = []
                for index in range(keyframe_count):
                    frame, texture_index, palette_index = struct.unpack_from(
                        "<HBB", data, start + index * 4
                    )
                    if texture_index >= texture_count or palette_index >= palette_count:
                        keyframes = []
                        break
                    keyframes.append(
                        PatternKeyframe(
                            frame=int(frame),
                            texture_index=int(texture_index),
                            palette_index=int(palette_index),
                        )
                    )
                if keyframes:
                    tracks.append(
                        NitroPatternMaterialTrack(
                            material=material,
                            keyframes=tuple(keyframes),
                        )
                    )
            if tracks:
                clips.append(
                    NitroPatternClip(
                        name=fallback_name or f"pattern_{len(clips)}",
                        frame_count=int(frame_count),
                        texture_names=texture_names,
                        palette_names=palette_names,
                        tracks=tuple(tracks),
                    )
                )
    return tuple(clips)


def _portable_image_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")


def attach_btp0_pattern_motion(
    glb_path: Path,
    btp0_files: list[bytes],
    image_paths: list[Path],
) -> int:
    """Attach embedded prop NSBTP frames for RAE preview and .tile export."""
    glb = read_glb(glb_path)
    materials = glb.json.get("materials") or []
    material_names: dict[str, list[tuple[int, str]]] = {}
    for index, material in enumerate(materials):
        if isinstance(material, dict) and material.get("name"):
            exact = str(material["name"])
            material_names.setdefault(exact.casefold(), []).append((index, exact))
    image_by_name: dict[str, str] = {}
    for path in image_paths:
        if path.suffix.casefold() != ".png" or not path.is_file():
            continue
        try:
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        except OSError:
            continue
        image_by_name.setdefault(
            _portable_image_key(path.stem),
            f"data:image/png;base64,{encoded}",
        )

    parsed: list[NitroPatternClip] = []
    for data in btp0_files:
        try:
            parsed.extend(parse_btp0_pattern_motion(data))
        except (ValueError, struct.error):
            continue
    exact_track_names = {
        track.material.casefold()
        for clip in parsed
        for track in clip.tracks
    }
    generated_clips: list[dict] = []
    for clip in parsed:
        tracks_json: list[dict] = []
        for track in clip.tracks:
            keyframes = []
            for keyframe in track.keyframes:
                texture_name = clip.texture_names[keyframe.texture_index]
                image = image_by_name.get(_portable_image_key(texture_name))
                if not image:
                    continue
                keyframes.append(
                    {
                        "frame": keyframe.frame,
                        "name": texture_name,
                        "image": image,
                    }
                )
            if not keyframes:
                continue
            for _material_index, exact_name in _animation_material_matches(
                material_names,
                track.material,
                exact_track_names,
            ):
                tracks_json.append(
                    {
                        "material": exact_name,
                        "frameCount": clip.frame_count,
                        "frameRate": NITRO_PATTERN_FRAME_RATE,
                        "imageKeyframes": keyframes,
                    }
                )
        if tracks_json:
            generated_clips.append(
                {
                    "id": f"pattern_{_portable_image_key(clip.name) or len(generated_clips)}",
                    "name": f"{clip.name} texture frames",
                    "frameCount": clip.frame_count,
                    "loop": True,
                    "tracks": tracks_json,
                }
            )
    if not generated_clips:
        return 0

    rae = glb.json.setdefault("extras", {}).setdefault("rae", {})
    motion = rae.setdefault(
        "mapMaterialMotion",
        {
            "frameRate": RESORT_MAP_FRAME_RATE,
            "sourceFrameRate": NITRO_SOURCE_FRAME_RATE,
            "clips": [],
        },
    )
    clips = motion.setdefault("clips", [])
    if clips:
        default_id = str(motion.get("defaultClip") or clips[0].get("id") or "")
        default = next(
            (clip for clip in clips if str(clip.get("id") or "") == default_id),
            clips[0],
        )
        by_material = {
            str(track.get("material") or "").casefold(): track
            for track in default.setdefault("tracks", [])
        }
        for generated in generated_clips:
            for track in generated["tracks"]:
                existing = by_material.get(str(track["material"]).casefold())
                if existing is None:
                    default["tracks"].append(track)
                    by_material[str(track["material"]).casefold()] = track
                else:
                    existing.update({key: value for key, value in track.items() if key != "material"})
            default["frameCount"] = max(
                int(default.get("frameCount") or 1),
                int(generated.get("frameCount") or 1),
            )
    else:
        clips.append(generated_clips[0])
        motion["defaultClip"] = generated_clips[0]["id"]
    known = {str(clip.get("id") or "") for clip in clips}
    clips.extend(clip for clip in generated_clips if clip["id"] not in known)
    motion.setdefault("frameRate", RESORT_MAP_FRAME_RATE)
    motion.setdefault("sourceFrameRate", NITRO_SOURCE_FRAME_RATE)
    glb.write(glb_path)
    return sum(len(clip["tracks"]) for clip in generated_clips)


def _align4(value: int) -> int:
    return (value + 3) & ~3


def parse_gen5_pattern_container(data: bytes) -> tuple[PatternTrack, ...]:
    """Decode the custom BW/BW2 area-pattern container from ``a/0/6/9``."""
    if len(data) < 12:
        raise ValueError("Gen 5 pattern-animation container is truncated")
    entry_count = struct.unpack_from("<I", data, 0)[0]
    table_end = 4 + entry_count * 8
    if entry_count <= 0 or table_end > len(data):
        raise ValueError("Gen 5 pattern-animation offset table is invalid")
    offsets = struct.unpack_from(f"<{entry_count * 2}I", data, 4)
    if any(offset < table_end or offset >= len(data) for offset in offsets):
        raise ValueError("Gen 5 pattern-animation resource offset is invalid")

    tracks: list[PatternTrack] = []
    for entry_index in range(entry_count):
        pattern_start = offsets[entry_index * 2]
        texture_start = offsets[entry_index * 2 + 1]
        pattern_end = texture_start
        if pattern_start + 4 > pattern_end:
            continue
        keyframe_count = struct.unpack_from("<I", data, pattern_start)[0]
        cursor = pattern_start + 4
        indices_end = cursor + keyframe_count * 2
        if keyframe_count <= 0 or indices_end > pattern_end:
            continue
        frame_indices = struct.unpack_from(f"<{keyframe_count}H", data, cursor)
        cursor = _align4(indices_end)
        if cursor + keyframe_count > pattern_end:
            continue
        texture_indices = data[cursor : cursor + keyframe_count]
        cursor = _align4(cursor + keyframe_count)
        if cursor + keyframe_count > pattern_end:
            continue
        palette_indices = data[cursor : cursor + keyframe_count]
        cursor = _align4(cursor + keyframe_count)
        if cursor + 4 > pattern_end:
            continue
        target_count = struct.unpack_from("<I", data, cursor)[0]
        cursor += 4
        if target_count <= 0 or cursor + target_count > pattern_end:
            continue
        target_starts = list(data[cursor : cursor + target_count]) + [keyframe_count]
        cursor = _align4(cursor + target_count)
        if cursor + 4 > pattern_end:
            continue
        frame_count = struct.unpack_from("<I", data, cursor)[0]
        if frame_count <= 0:
            continue
        texture_end = offsets[(entry_index + 1) * 2] if entry_index + 1 < entry_count else len(data)
        texture_data = bytes(data[texture_start:texture_end])
        for target_index in range(target_count):
            start = target_starts[target_index]
            end = target_starts[target_index + 1]
            if not (0 <= start < end <= keyframe_count):
                continue
            tracks.append(
                PatternTrack(
                    frame_count=frame_count,
                    keyframes=tuple(
                        PatternKeyframe(
                            frame=int(frame_indices[index]),
                            texture_index=int(texture_indices[index]),
                            palette_index=int(palette_indices[index]),
                        )
                        for index in range(start, end)
                    ),
                    texture_data=texture_data,
                )
            )
    return tuple(tracks)


def _pattern_family(value: str) -> str:
    cleaned = str(value or "").casefold().replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = cleaned.split("__", 1)[0]
    cleaned = re.sub(r"[._-]\d+$", "", cleaned)
    return re.sub(r"[^a-z0-9]+", "", cleaned)


def _matching_pattern_materials(materials: list[str], family: str) -> list[str]:
    """Match named texture families without treating placeholders as wildcards."""
    family = _pattern_family(family)
    if not family:
        return []
    exact = [name for name in materials if _pattern_family(name) == family]
    if exact:
        return exact
    candidates: list[str] = []
    for name in materials:
        candidate_family = _pattern_family(name)
        if not candidate_family:
            continue
        if candidate_family.startswith(family) or family.startswith(candidate_family):
            candidates.append(name)
    return candidates


def _png_data_url(image) -> str:
    payload = io.BytesIO()
    image.to_pil().save(payload, format="PNG")
    return "data:image/png;base64," + base64.b64encode(payload.getvalue()).decode("ascii")


def attach_gen5_pattern_motion(glb_path: Path, pattern_data: bytes) -> int:
    """Attach Gen 5 texture swaps to the same NDS material-motion preview API."""
    from .nitro.decode import decode_btx_images

    try:
        parsed = parse_gen5_pattern_container(pattern_data)
    except (ValueError, struct.error):
        return 0
    glb = read_glb(glb_path)
    materials = [
        str(material.get("name") or "")
        for material in (glb.json.get("materials") or [])
        if isinstance(material, dict) and material.get("name")
    ]
    pattern_clips: list[dict] = []
    matched_tracks: list[dict] = []
    for parsed_index, track in enumerate(parsed):
        images = decode_btx_images(track.texture_data, max_images=256, mode="resolved")
        if not images:
            continue
        first_index = track.keyframes[0].texture_index
        if first_index >= len(images):
            continue
        family = _pattern_family(images[first_index].name)
        candidates = _matching_pattern_materials(materials, family)
        if not candidates:
            continue
        material = candidates[0]
        keyframes: list[dict] = []
        for keyframe in track.keyframes:
            if keyframe.texture_index >= len(images):
                continue
            image = images[keyframe.texture_index]
            keyframes.append(
                {
                    "frame": keyframe.frame,
                    "name": image.name,
                    "image": _png_data_url(image),
                }
            )
        if not keyframes:
            continue
        body = {
            "material": material,
            "frameCount": track.frame_count,
            "imageKeyframes": keyframes,
        }
        matched_tracks.append(body)
        clip_id = f"pattern_{re.sub(r'[^A-Za-z0-9_-]+', '_', material).strip('_') or parsed_index}"
        pattern_clips.append(
            {
                "id": clip_id,
                "name": f"{material} texture frames",
                "frameCount": track.frame_count,
                "loop": True,
                "tracks": [body],
            }
        )
    if not matched_tracks:
        return 0

    rae = glb.json.setdefault("extras", {}).setdefault("rae", {})
    motion = rae.setdefault(
        "mapMaterialMotion",
        {
            "frameRate": RESORT_MAP_FRAME_RATE,
            "sourceFrameRate": NITRO_SOURCE_FRAME_RATE,
            "clips": [],
        },
    )
    motion.setdefault("frameRate", RESORT_MAP_FRAME_RATE)
    motion.setdefault("sourceFrameRate", NITRO_SOURCE_FRAME_RATE)
    clips = motion.setdefault("clips", [])
    if clips:
        default_id = str(motion.get("defaultClip") or clips[0].get("id") or "")
        default_clip = next((clip for clip in clips if str(clip.get("id") or "") == default_id), clips[0])
        by_material = {
            str(track.get("material") or "").casefold(): track
            for track in default_clip.setdefault("tracks", [])
        }
        for track in matched_tracks:
            existing = by_material.get(str(track["material"]).casefold())
            if existing is None:
                default_clip["tracks"].append(track)
            else:
                existing.update({key: value for key, value in track.items() if key != "material"})
        default_clip["frameCount"] = max(
            int(default_clip.get("frameCount") or 0),
            *(int(track["frameCount"]) for track in matched_tracks),
        )
    else:
        combined = {
            "id": "area_pattern",
            "name": "Area texture patterns",
            "frameCount": max(int(track["frameCount"]) for track in matched_tracks),
            "loop": True,
            "tracks": matched_tracks,
        }
        clips.append(combined)
        motion["defaultClip"] = combined["id"]
    known = {str(clip.get("id") or "") for clip in clips}
    clips.extend(clip for clip in pattern_clips if clip["id"] not in known)
    glb.write(glb_path)
    return len(matched_tracks)
