"""Texture flipbook sequences (name.N / name_N frames) for RAE preview and session export."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .texture_assignments import texture_key_for_path

SESSION_KEY = "texture_sequences"
DEFAULT_FRAME_DURATION_MS = 150
ANIMATION_FRAME_STYLE = "dot"
DEFAULT_PLAY_STATE = "play"


@dataclass(frozen=True, slots=True)
class TextureFrameFamily:
    sequence_base: str
    frame_style: str
    frames: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return normalize_material_spec(
            {
                "sequenceBase": self.sequence_base,
                "frameStyle": self.frame_style,
                "frames": list(self.frames),
                "frameDurationMs": DEFAULT_FRAME_DURATION_MS,
                "loop": True,
            }
        )


def normalize_material_spec(spec: dict[str, Any]) -> dict[str, Any]:
    out = dict(spec)
    frames = [str(frame).strip().casefold() for frame in (out.get("frames") or []) if str(frame).strip()]
    out["frames"] = frames
    out.pop("availableTextures", None)
    out["frameStyle"] = str(out.get("frameStyle") or ANIMATION_FRAME_STYLE)
    states = out.get("states")
    out["states"] = dict(states) if isinstance(states, dict) else {}
    active = str(out.get("activeState") or "").strip()
    if active and active not in out["states"]:
        out["activeState"] = next(iter(out["states"]), "")
    elif not active:
        out["activeState"] = ""
    out["frameDurationMs"] = int(out.get("frameDurationMs") or DEFAULT_FRAME_DURATION_MS)
    return out


def ensure_material_states(spec: dict[str, Any]) -> dict[str, Any]:
    return normalize_material_spec(spec)


def default_play_state(frames: list[str]) -> dict[str, dict[str, Any]]:
    frame_list = [str(frame).strip().casefold() for frame in frames if str(frame).strip()]
    if len(frame_list) < 2:
        return {}
    return {
        DEFAULT_PLAY_STATE: {
            "frames": list(frame_list),
            "animate": True,
            "loop": True,
            "speed": 1.0,
        }
    }


def ensure_default_play_state(spec: dict[str, Any]) -> dict[str, Any]:
    """Add a default 'play' state spanning all frames when none exist."""
    out = normalize_material_spec(spec)
    if out.get("states") or len(out.get("frames") or []) < 2:
        return out
    states = default_play_state(out["frames"])
    if not states:
        return out
    out["states"] = states
    out["activeState"] = DEFAULT_PLAY_STATE
    return out


def state_names(spec: dict[str, Any]) -> list[str]:
    states = spec.get("states")
    if not isinstance(states, dict):
        return []
    return [str(name) for name in states.keys()]


def state_spec(spec: dict[str, Any], state_name: str) -> dict[str, Any] | None:
    states = spec.get("states")
    if not isinstance(states, dict):
        return None
    key = str(state_name or "").strip().casefold()
    for name, body in states.items():
        if str(name).casefold() == key and isinstance(body, dict):
            return body
    return None


def playback_frames_for_spec(
    spec: dict[str, Any],
    *,
    state_name: str | None = None,
) -> tuple[list[str], bool, bool, int]:
    spec = normalize_material_spec(spec)
    active = str(state_name or spec.get("activeState") or "").strip().casefold()
    state_spec_body = state_spec(spec, active) if active else None
    if not isinstance(state_spec_body, dict) or not state_spec_body.get("frames"):
        return [], False, True, int(spec.get("frameDurationMs") or DEFAULT_FRAME_DURATION_MS)
    frames = [str(frame).strip().casefold() for frame in state_spec_body["frames"] if str(frame).strip()]
    animate = bool(state_spec_body.get("animate", len(frames) > 1))
    loop = bool(state_spec_body.get("loop", True))
    speed = max(0.05, float(state_spec_body.get("speed") or 1.0))
    base_duration = int(spec.get("frameDurationMs") or DEFAULT_FRAME_DURATION_MS)
    duration_ms = max(16, int(base_duration / speed))
    return frames, animate, loop, duration_ms


def preview_frame_for_spec(spec: dict[str, Any], *, state_name: str | None = None) -> str | None:
    name = state_name or normalize_material_spec(spec).get("activeState")
    if name:
        frames, _animate, _loop, _duration = playback_frames_for_spec(spec, state_name=str(name))
        return frames[0] if frames else None
    frames = normalize_material_spec(spec).get("frames") or []
    return frames[0] if frames else None


def is_numbered_frame_key(key: str) -> bool:
    """True for ``name.N`` or apicula ``name_N`` texture keys."""
    _base, index, _style = parse_frame_texture_key(key)
    return index is not None


def is_dot_animation_frame_key(key: str) -> bool:
    """True for flipbook keys in ``name.N`` form."""
    _base, index, style = parse_frame_texture_key(key)
    return index is not None and style == "dot"


def canonical_frame_key(key: str) -> str:
    """Canonical ``name.N`` key, or the input unchanged when not a dot frame."""
    key = str(key or "").strip().casefold()
    if not key:
        return key
    base, index, style = parse_frame_texture_key(key)
    if index is None or style != "dot":
        return key
    return f"{base}.{index}"


def frame_key_lookup_variants(key: str) -> list[str]:
    """Filename stems that may resolve a texture key on disk."""
    key = str(key or "").strip().casefold()
    variants: list[str] = []
    base, index, style = parse_frame_texture_key(key)
    if style == "dot" and index is not None:
        dot = f"{base}.{index}"
        under = f"{base}_{index}"
        for candidate in (key, dot, under):
            if candidate and candidate not in variants:
                variants.append(candidate)
        return variants
    if style == "underscore" and index is not None:
        under = f"{base}_{index}"
        dot = f"{base}.{index}"
        for candidate in (key, under, dot):
            if candidate and candidate not in variants:
                variants.append(candidate)
        return variants
    if key and key not in variants:
        variants.append(key)
    return variants


def list_playback_options(materials_spec: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Viewport targets: user-defined states (animated or single-frame set)."""
    options: list[dict[str, Any]] = []
    for material_name, raw_spec in sorted(materials_spec.items()):
        spec = normalize_material_spec(raw_spec)
        if str(spec.get("frameStyle") or "") != ANIMATION_FRAME_STYLE:
            continue
        for state_name in state_names(spec):
            frames, animate, _loop, _duration = playback_frames_for_spec(spec, state_name=state_name)
            if not frames:
                continue
            options.append(
                {
                    "material": material_name,
                    "state": state_name,
                    "animate": bool(animate and len(frames) >= 2),
                }
            )
    state_name_counts: dict[str, int] = {}
    for option in options:
        state = str(option.get("state") or "")
        state_name_counts[state] = state_name_counts.get(state, 0) + 1
    for option in options:
        state = str(option.get("state") or "")
        material = str(option.get("material") or "")
        if len(options) > 1 and state_name_counts.get(state, 0) > 1:
            option["label"] = f"{material} · {state}"
        else:
            option["label"] = state
    return options


def empty_material_spec(*, sequence_base: str = "") -> dict[str, Any]:
    return normalize_material_spec(
        {
            "sequenceBase": sequence_base,
            "frameStyle": ANIMATION_FRAME_STYLE,
            "frames": [],
            "states": {},
            "activeState": "",
        }
    )


def material_has_animation_frames(spec: dict[str, Any]) -> bool:
    """True when a material has a detected numbered frame pool (2+ frames)."""
    spec = normalize_material_spec(spec)
    frames = [frame for frame in (spec.get("frames") or []) if is_numbered_frame_key(str(frame))]
    return str(spec.get("frameStyle") or "") == ANIMATION_FRAME_STYLE and len(frames) >= 2


def material_has_playable_states(spec: dict[str, Any]) -> bool:
    spec = normalize_material_spec(spec)
    for state_name in state_names(spec):
        frames, _animate, _loop, _duration = playback_frames_for_spec(spec, state_name=state_name)
        if frames:
            return True
    return False


_DOT_FRAME = re.compile(r"^(.+)\.(\d+)$", re.IGNORECASE)
_UNDERSCORE_FRAME = re.compile(r"^(.+)_(\d+)$", re.IGNORECASE)


def parse_frame_texture_key(key: str) -> tuple[str, int | None, str | None]:
    key = str(key or "").strip().casefold()
    if not key:
        return "", None, None
    base_key = key.split("__", 1)[0]
    match = _DOT_FRAME.match(base_key)
    if match:
        return match.group(1).casefold(), int(match.group(2)), "dot"
    match = _UNDERSCORE_FRAME.match(base_key)
    if match and match.group(2).isdigit():
        return match.group(1).casefold(), int(match.group(2)), "underscore"
    return base_key, None, None


def sequence_base_for_texture_key(key: str) -> str | None:
    """Shared prefix for numbered frames bound to a material texture."""
    key = str(key or "").strip().casefold()
    if not key:
        return None
    base, index, _style = parse_frame_texture_key(key)
    return base if index is not None else key


def _numbered_frame_key(base: str, index: int, style: str) -> str:
    if style == "underscore":
        return f"{base}_{index}"
    return f"{base}.{index}"


def discover_numbered_frames_for_texture(
    tex_key: str,
    available_keys: set[str] | list[str],
) -> list[str]:
    """Numbered siblings (``name.N`` / ``name_N``) for one material texture."""
    sequence_base = sequence_base_for_texture_key(tex_key)
    if not sequence_base:
        return []
    by_index: dict[int, str] = {}
    for raw in available_keys:
        key = str(raw or "").strip().casefold()
        if not key or key.isdigit():
            continue
        base, index, style = parse_frame_texture_key(key)
        if index is None or base != sequence_base:
            continue
        frame_key = _numbered_frame_key(base, index, style)
        existing = by_index.get(index)
        if existing is None:
            by_index[index] = frame_key
        elif style == "underscore":
            by_index[index] = frame_key
    if len(by_index) < 2:
        return []
    return [by_index[idx] for idx in sorted(by_index)]


def discover_dot_frames_for_texture(
    tex_key: str,
    available_keys: set[str] | list[str],
) -> list[str]:
    """Backward-compatible alias; returns canonical dot keys when possible."""
    frames = discover_numbered_frames_for_texture(tex_key, available_keys)
    return [canonical_frame_key(frame) if parse_frame_texture_key(frame)[2] == "underscore" else frame for frame in frames]


def detect_texture_frame_families(texture_keys: set[str] | list[str]) -> dict[str, TextureFrameFamily]:
    """Group ``name.N`` dot-frame textures only (apicula ``name_N`` is ignored)."""
    grouped: dict[str, dict[int, str]] = {}
    for raw_key in texture_keys:
        key = str(raw_key or "").strip().casefold()
        if not key:
            continue
        base, index, style = parse_frame_texture_key(key)
        if index is None or style != "dot":
            continue
        canonical = f"{base}.{index}"
        grouped.setdefault(base, {})[index] = canonical

    families: dict[str, TextureFrameFamily] = {}
    for base, by_index in grouped.items():
        if len(by_index) < 2:
            continue
        frames = tuple(by_index[idx] for idx in sorted(by_index))
        families[base] = TextureFrameFamily(
            sequence_base=base,
            frame_style=ANIMATION_FRAME_STYLE,
            frames=frames,
        )
    return families


def family_for_texture_key(
    texture_key: str,
    families: dict[str, TextureFrameFamily],
) -> TextureFrameFamily | None:
    key = str(texture_key or "").strip().casefold()
    if not key:
        return None
    dot_key = canonical_frame_key(key)
    for family in families.values():
        if dot_key in family.frames:
            return family
    base, index, style = parse_frame_texture_key(key)
    if style == "underscore" and index is not None:
        underscored_as_dot = f"{base}.{index}"
        for family in families.values():
            if underscored_as_dot in family.frames:
                return family
    return None


def detect_material_sequences(
    mesh_labels: list[str],
    mesh_paths: list[Path | None],
    *,
    assignments: dict[str, str],
    available_texture_keys: set[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for label, path in zip(mesh_labels, mesh_paths):
        if not label:
            continue
        tex_key = assignments.get(label, "").strip().casefold()
        if not tex_key and path is not None:
            tex_key = texture_key_for_path(path)
        if not tex_key:
            continue
        frames = discover_numbered_frames_for_texture(tex_key, available_texture_keys)
        if len(frames) < 2:
            continue
        sequence_base = sequence_base_for_texture_key(tex_key) or sequence_base_for_texture_key(frames[0]) or ""
        out[label] = normalize_material_spec(
            {
                "sequenceBase": sequence_base,
                "frameStyle": ANIMATION_FRAME_STYLE,
                "frames": frames,
                "frameDurationMs": DEFAULT_FRAME_DURATION_MS,
                "loop": True,
            }
        )
    return out


def empty_sequences() -> dict[str, dict[str, Any]]:
    return {}


def load_texture_sequences(manifest: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    if not manifest:
        return empty_sequences()
    raw = manifest.get(SESSION_KEY)
    if not isinstance(raw, dict):
        return empty_sequences()
    out: dict[str, dict[str, Any]] = {}
    for asset_id, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        materials = payload.get("materials")
        if not isinstance(materials, dict) or not materials:
            continue
        cleaned_materials: dict[str, dict[str, Any]] = {}
        for mat_name, spec in materials.items():
            if not isinstance(spec, dict):
                continue
            if str(spec.get("frameStyle") or ANIMATION_FRAME_STYLE) != ANIMATION_FRAME_STYLE:
                continue
            frames = spec.get("frames")
            if not isinstance(frames, list) or len(frames) < 2:
                continue
            frame_keys = [
                str(frame).strip().casefold()
                for frame in frames
                if str(frame).strip() and is_numbered_frame_key(str(frame))
            ]
            if len(frame_keys) < 2:
                continue
            cleaned: dict[str, Any] = {
                "sequenceBase": str(spec.get("sequenceBase") or texture_key_for_path(Path(frame_keys[0]))).strip(),
                "frameStyle": ANIMATION_FRAME_STYLE,
                "frames": frame_keys,
                "frameDurationMs": int(spec.get("frameDurationMs") or DEFAULT_FRAME_DURATION_MS),
                "loop": bool(spec.get("loop", True)),
            }
            raw_states = spec.get("states")
            cleaned_states: dict[str, dict[str, Any]] = {}
            if isinstance(raw_states, dict):
                for state_name, state_spec_body in raw_states.items():
                    if not isinstance(state_spec_body, dict):
                        continue
                    state_frames = [
                        str(frame).strip().casefold()
                        for frame in (state_spec_body.get("frames") or [])
                        if str(frame).strip()
                    ]
                    if not state_frames:
                        continue
                    cleaned_states[str(state_name).strip()] = {
                        "frames": state_frames,
                        "animate": bool(state_spec_body.get("animate", len(state_frames) > 1)),
                        "loop": bool(state_spec_body.get("loop", True)),
                        "speed": float(state_spec_body.get("speed") or 1.0),
                    }
            cleaned["states"] = cleaned_states
            active = str(spec.get("activeState") or "").strip()
            cleaned["activeState"] = active if active in cleaned_states else ""
            cleaned_materials[str(mat_name).strip()] = normalize_material_spec(cleaned)
        if cleaned_materials:
            out[str(asset_id)] = {"materials": cleaned_materials}
    return out


def resolve_frame_path(frame_key: str, *, texture_by_name: dict[str, Path], fallback_paths: list[Path]) -> Path | None:
    variants = frame_key_lookup_variants(frame_key)
    if not variants:
        return None
    for variant in variants:
        direct = texture_by_name.get(variant)
        if direct is not None and direct.is_file():
            return direct
    variant_set = set(variants)
    for path in fallback_paths:
        if not path.is_file():
            continue
        stem = path.stem.casefold()
        if stem in variant_set or canonical_frame_key(stem) in variant_set:
            return path
    return None
