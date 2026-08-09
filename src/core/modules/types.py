from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol


class PreviewRoute(str, Enum):
    """Platform-neutral preview kinds (modules map their assets to these)."""

    MODEL = "model"
    TEXTURE_BTX0 = "texture_btx0"
    TEXTURE_2D = "texture_2d"
    TEXTURE_PNG = "texture_png"
    AUDIO = "audio"
    UNSUPPORTED = "unsupported"

    # Legacy aliases — same values, do not add new platform-prefixed routes.
    NDS_MODEL = "model"
    MOBILE_MODEL = "model"
    NDS_TEXTURE_BTX0 = "texture_btx0"
    NDS_TEXTURE_2D = "texture_2d"
    NDS_PNG = "texture_png"
    NDS_AUDIO = "audio"


class ExportRoute(str, Enum):
    STANDARD = "standard"
    RAW_BUNDLE = "raw_bundle"
    UNSUPPORTED = "unsupported"

    # Legacy aliases
    NDS_STANDARD = "standard"
    MOBILE_RAW = "raw_bundle"


@dataclass(slots=True)
class ProfileSummary:
    rom_game_code: str = ""
    rom_title: str = ""
    profile_text: str = ""
    current_mapping: object | None = None


@dataclass(slots=True)
class PreviewContext:
    """Opaque UI context passed into platform modules (typically the main window)."""

    window: Any
    manual: bool = True
    force: bool = False


@dataclass(slots=True)
class ModelPreviewBindings:
    glb_path: str = ""
    mesh_name: str = ""
    source_bundle: str = ""
    texture_paths: list[str] = field(default_factory=list)
    texture_by_name: dict[str, str] = field(default_factory=dict)
    material_to_texture: dict[str, str] = field(default_factory=dict)
    texture_bind_order: list[str] = field(default_factory=list)
    texture_sheet_entries: list[dict[str, str]] = field(default_factory=list)
    preview_backend: str = ""
    warnings: list[str] = field(default_factory=list)


Progress = Callable[[str], None]
