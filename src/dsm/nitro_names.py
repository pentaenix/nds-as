from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Iterable

from .scanner import Asset

# Nitro NSBMD/NSBTX files use null-padded 16-byte ASCII names in their
# dictionaries. This parser remains lightweight, but it is intentionally stricter
# than a plain ASCII-string scan. Earlier builds accepted repeating fill-like
# strings such as "333333333333DDDD", which created false texture matches.
_BAD_NAMES = {
    "BMD0", "BTX0", "MDL0", "TEX0", "JNT0", "PAT0", "SRT0",
    "NARC", "BTA0", "BTP0", "BCA0", "BMA0", "BVA0", "BPC0",
    "RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR",
}
_NAME_RE = re.compile(rb"[A-Za-z_][A-Za-z0-9_.\-]{2,15}")
MAX_NAME_SCAN_BYTES = 4 * 1024 * 1024


def _looks_like_filler(text: str) -> bool:
    # Nitro dictionaries are 16-byte name slots, but a raw binary scan can also
    # pick up padding, GPU commands, repeated flags, and header-looking junk.
    # Treat tiny/repetitive strings as weak evidence; using them as a “confirmed”
    # texture match is what made candidates such as DDD3 / wwww look reliable.
    body = re.sub(r"[^A-Za-z0-9]", "", text)
    if not body:
        return True
    if len(set(body)) <= 2 and len(body) >= 4:
        return True
    if re.search(r"(.)\1{3,}", body):
        return True
    if re.fullmatch(r"[0-9A-Fa-f]{4,16}", body):
        return True
    counts = {ch: body.count(ch) for ch in set(body)}
    max_run = max(counts.values()) / max(1, len(body))
    if len(body) >= 5 and max_run >= 0.62:
        return True
    entropy = -sum((n / len(body)) * math.log2(n / len(body)) for n in counts.values())
    return entropy < 1.35 and len(body) >= 5


def _clean_name(raw: bytes) -> str | None:
    raw = raw.split(b"\x00", 1)[0].strip()
    if not (3 <= len(raw) <= 16):
        return None
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return None
    if text in _BAD_NAMES:
        return None
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.\-]{2,15}", text):
        return None
    letters = sum(1 for ch in text if ch.isalpha())
    if letters < 2:
        return None
    if len(text) < 4 and "_" not in text and "-" not in text:
        return None
    if _looks_like_filler(text):
        return None

    # Four-character all-uppercase/digit tokens are common false positives in
    # Pokémon map files. Real texture names tend to look like words (kabe, grass),
    # include underscores/suffixes (kabe_pl), or have longer semantic stems. Keep
    # short lowercase names, but demote header/control-looking uppercase tokens.
    compact = text.replace("_", "").replace("-", "").replace(".", "")
    if len(compact) <= 4 and compact.upper() == compact and any(ch.isdigit() for ch in compact):
        return None
    if len(compact) <= 4 and compact.upper() == compact and len(set(compact)) <= 3:
        return None
    return text


def _dictionary_slot_names(view: bytes) -> set[str]:
    """Extract plausible 16-byte Nitro dictionary names.

    This is still heuristic, but scanning 16-byte slots catches many real
    NSBMD/NSBTX dictionaries while rejecting most alignment noise.
    """
    names: set[str] = set()
    for off in range(0, max(0, len(view) - 16) + 1, 16):
        name = _clean_name(view[off:off + 16])
        if name:
            names.add(name)
    return names


def extract_nitro_names(data: bytes) -> set[str]:
    """Extract plausible Nitro dictionary names from an NSBxx blob.

    BMD0 materials often reference texture/palette names, while BTX0 archives
    store texture and palette dictionaries. Matching these names is useful, but
    only if false positives are aggressively filtered.
    """
    view = data[:MAX_NAME_SCAN_BYTES]
    names = _dictionary_slot_names(view)

    # Fallback for unaligned names in unusual containers. Because this can find
    # ordinary words inside binary data, _clean_name is intentionally strict.
    for match in _NAME_RE.finditer(view):
        name = _clean_name(match.group(0))
        if name:
            names.add(name)

    return names


def format_display_names(names: Iterable[str], *, limit: int = 2) -> str:
    """Format Nitro dictionary names for browser rows."""
    unique = sorted({n.strip() for n in names if n and n.strip()})
    if not unique:
        return ""
    if len(unique) == 1:
        return unique[0]
    shown = ", ".join(unique[:limit])
    extra = len(unique) - limit
    if extra > 0:
        shown += f" (+{extra} more)"
    return shown


def asset_filename_label(virtual_path: str) -> str:
    """Return the leaf filename from a ROM virtual path."""
    name = virtual_path.replace("\\", "/").rstrip("/").split("/")[-1]
    return name or virtual_path


def asset_browser_name(asset: Asset, *, scan_bytes: bool = True) -> str:
    """Best label for browser Name columns: dictionary name, then filename."""
    if scan_bytes and len(asset.data) <= 8 * 1024 * 1024:
        label = format_display_names(asset_dictionary_names(asset))
        if label:
            return label
        label = format_display_names(extract_nitro_names(asset.data))
        if label:
            return label
    return asset_filename_label(asset.virtual_path)


def asset_dictionary_names(asset: Asset) -> list[str]:
    """Return ordered Nitro dictionary names for browser display."""
    from .nitro_models import parse_nsbmd_manifest
    from .nitro_textures import parse_tex0

    if asset.magic == "BMD0":
        manifest = parse_nsbmd_manifest(asset.data)
        if manifest:
            names: list[str] = []
            for bucket in (manifest.model_names, manifest.raw_names):
                for name in bucket:
                    if name and name not in names:
                        names.append(name)
            if names:
                return names
    elif asset.magic == "BTX0":
        tex0 = parse_tex0(asset.data)
        if tex0:
            texture_names = [t.name for t in tex0.textures if t.name]
            if texture_names:
                return texture_names
            palette_names = [p.name for p in tex0.palettes if p.name]
            if palette_names:
                return palette_names
    return sorted(extract_nitro_names(asset.data))


@dataclass(slots=True)
class TextureMatch:
    asset: Asset
    overlapping_names: tuple[str, ...]

    @property
    def score_bonus(self) -> int:
        # More shared names is much stronger than path proximity.
        return min(90, 35 + len(self.overlapping_names) * 5)


def texture_name_matches(model: Asset, candidates: Iterable[Asset]) -> list[TextureMatch]:
    model_names = extract_nitro_names(model.data)
    matches: list[TextureMatch] = []
    if not model_names:
        return matches
    for asset in candidates:
        if asset.magic != "BTX0":
            continue
        overlap = sorted(model_names & extract_nitro_names(asset.data))
        if overlap:
            matches.append(TextureMatch(asset=asset, overlapping_names=tuple(overlap)))
    matches.sort(key=lambda m: (-len(m.overlapping_names), m.asset.virtual_path))
    return matches


def texture_match_report(model: Asset, candidates: Iterable[Asset], *, limit: int = 40) -> str:
    model_names = sorted(extract_nitro_names(model.data))
    matches = texture_name_matches(model, candidates)
    lines = [
        f"Model: {model.virtual_path}",
        f"Model candidate names ({len(model_names)}): " + (", ".join(model_names[:80]) if model_names else "none found"),
        "",
        f"BTX0 texture files with matching names: {len(matches)}",
    ]
    for match in matches[:limit]:
        names = ", ".join(match.overlapping_names[:20])
        if len(match.overlapping_names) > 20:
            names += ", ..."
        lines.append(f"- {match.asset.virtual_path}")
        lines.append(f"  shared names: {names}")
    if len(matches) > limit:
        lines.append(f"... {len(matches) - limit} more match(es) omitted")
    if not matches:
        lines.extend([
            "",
            "No BTX0 file shared a reliable Nitro dictionary name with this BMD0.",
            "That can mean the correct texture archive is selected by a map/area table, the archive was not found, or this model uses embedded/no textures.",
        ])
    return "\n".join(lines) + "\n"
