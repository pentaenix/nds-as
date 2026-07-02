"""Dominant color buckets for EasyFind grouping."""
from __future__ import annotations

import colorsys
from typing import Iterable

BUCKET_ORDER: tuple[str, ...] = (
    "red",
    "orange",
    "yellow",
    "green",
    "cyan",
    "blue",
    "purple",
    "pink",
    "brown",
    "gray",
    "white",
    "black",
    "neutral",
    "audio",
    "unknown",
)

BUCKET_LABELS: dict[str, str] = {
    "red": "RED",
    "orange": "ORANGE",
    "yellow": "YELLOW",
    "green": "GREEN",
    "cyan": "CYAN",
    "blue": "BLUE",
    "purple": "PURPLE",
    "pink": "PINK",
    "brown": "BROWN",
    "gray": "GRAY",
    "white": "WHITE",
    "black": "BLACK",
    "neutral": "NEUTRAL",
    "audio": "AUDIO",
    "unknown": "UNKNOWN",
}

# Border / label accent colors (r, g, b)
# Model thumbnail composite in finalize_easyfind_model_thumbnail (also three.js 0x3a3a3a).
MODEL_THUMB_BACKGROUND_RGB: tuple[int, int, int] = (58, 58, 58)
MODEL_THUMB_BACKGROUND_TOLERANCE = 3

# Accent colors kept for secondary-bucket indexing (e.g. palm fronds on a brown trunk).
SECONDARY_BUCKET_MAX = 10
SECONDARY_BUCKET_MIN_SHARE = 0.035

BUCKET_RGB: dict[str, tuple[int, int, int]] = {
    "red": (220, 70, 70),
    "orange": (230, 140, 55),
    "yellow": (230, 210, 60),
    "green": (80, 190, 90),
    "cyan": (70, 200, 210),
    "blue": (80, 140, 230),
    "purple": (170, 100, 220),
    "pink": (230, 120, 180),
    "brown": (160, 110, 70),
    "gray": (150, 150, 150),
    "white": (235, 235, 235),
    "black": (70, 70, 70),
    "neutral": (130, 130, 130),
    "audio": (120, 170, 200),
    "unknown": (110, 110, 110),
}


def bucket_label(bucket: str) -> str:
    return BUCKET_LABELS.get(bucket, bucket.replace("_", " ").upper())


def bucket_rgb(bucket: str) -> tuple[int, int, int]:
    return BUCKET_RGB.get(bucket, BUCKET_RGB["unknown"])


def _matches_model_thumb_background(r: int, g: int, b: int) -> bool:
    """True when a pixel matches the EasyFind model thumbnail matte."""
    br, bg, bb = MODEL_THUMB_BACKGROUND_RGB
    tol = MODEL_THUMB_BACKGROUND_TOLERANCE
    return abs(r - br) <= tol and abs(g - bg) <= tol and abs(b - bb) <= tol


def _hue_bucket(h: float, s: float, v: float) -> str:
    if v < 0.12:
        return "black"
    if v > 0.92 and s < 0.12:
        return "white"
    if s < 0.14:
        return "gray"
    deg = h * 360.0
    if deg < 15 or deg >= 345:
        return "red"
    if deg < 40:
        return "orange"
    if deg < 65:
        return "yellow"
    if deg < 150:
        return "green"
    if deg < 195:
        return "cyan"
    if deg < 250:
        return "blue"
    if deg < 290:
        return "purple"
    if deg < 330:
        return "pink"
    return "brown"


def dominant_bucket_from_rgba(
    rgba: bytes,
    width: int,
    height: int,
    *,
    sample_stride: int = 2,
) -> tuple[str, list[str], list[str], str, str, float | None]:
    """Return dominant bucket, hex colors, secondary buckets, brightness, saturation, transparent ratio."""
    if width <= 0 or height <= 0 or len(rgba) < width * height * 4:
        return "unknown", [], [], "mid", "low", None

    counts: dict[str, int] = {}
    opaque = 0
    total = 0
    lum_sum = 0.0
    sat_sum = 0.0
    hex_colors: list[str] = []

    for y in range(0, height, sample_stride):
        row = y * width * 4
        for x in range(0, width, sample_stride):
            i = row + x * 4
            r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
            if a < 32:
                total += 1
                continue
            if _matches_model_thumb_background(r, g, b):
                continue
            total += 1
            opaque += 1
            rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
            h, s, v = colorsys.rgb_to_hsv(rf, gf, bf)
            bucket = _hue_bucket(h, s, v)
            counts[bucket] = counts.get(bucket, 0) + 1
            lum_sum += v
            sat_sum += s
            if len(hex_colors) < 5:
                hx = f"#{r:02x}{g:02x}{b:02x}"
                if hx not in hex_colors:
                    hex_colors.append(hx)

    if not counts:
        return "neutral", hex_colors, [], "mid", "low", 1.0 if total else None

    dominant = max(counts, key=lambda k: (counts[k], -BUCKET_ORDER.index(k) if k in BUCKET_ORDER else 99))
    secondary = _secondary_buckets_from_counts(counts, dominant)
    avg_lum = lum_sum / max(1, opaque)
    avg_sat = sat_sum / max(1, opaque)
    brightness = "dark" if avg_lum < 0.35 else "bright" if avg_lum > 0.7 else "mid"
    saturation = "high" if avg_sat > 0.45 else "low" if avg_sat < 0.18 else "mid"
    transparent_ratio = 1.0 - (opaque / total) if total else None
    return dominant, hex_colors, secondary, brightness, saturation, transparent_ratio


def _secondary_buckets_from_counts(counts: dict[str, int], dominant: str) -> list[str]:
    """Non-dominant buckets ranked by pixel share, with a minimum accent threshold."""
    opaque = sum(counts.values())
    if opaque <= 0:
        return []
    ranked = sorted(
        ((bucket, counts[bucket]) for bucket in counts if bucket != dominant),
        key=lambda item: (
            -item[1],
            BUCKET_ORDER.index(item[0]) if item[0] in BUCKET_ORDER else 99,
        ),
    )
    secondary: list[str] = []
    for bucket, count in ranked:
        share = count / opaque
        if len(secondary) < SECONDARY_BUCKET_MAX or share >= SECONDARY_BUCKET_MIN_SHARE:
            secondary.append(bucket)
        elif len(secondary) >= SECONDARY_BUCKET_MAX:
            break
    return secondary


def bucket_for_node_kind(node_kind: str, magic: str = "") -> str:
    if node_kind == "audio":
        return "audio"
    if magic.upper() == "RLCN":
        return "neutral"
    return "unknown"
