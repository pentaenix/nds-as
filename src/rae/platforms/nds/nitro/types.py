"""Nitro TEX0/BTX0 data structures."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover
    Image = None
    ImageDraw = None


@dataclass(slots=True)
class DecodedImage:
    name: str
    width: int
    height: int
    rgba: bytes
    source: str
    format_id: int | None = None
    palette_name: str | None = None

    def to_pil(self):
        if Image is None:
            raise RuntimeError("Pillow is required to save/view decoded images. Install requirements.txt.")
        return Image.frombytes("RGBA", (self.width, self.height), self.rgba)

    def save_png(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        self.to_pil().save(out)
        return out


@dataclass(slots=True)
class NitroNameEntry:
    name: str
    data: bytes
    entry_offset: int = 0
    list_offset: int = 0


@dataclass(slots=True)
class TextureEntry:
    name: str
    teximage_params: int
    unknown: int

    @property
    def offset(self) -> int:
        return (self.teximage_params & 0xFFFF) << 3

    @property
    def width(self) -> int:
        return 8 << ((self.teximage_params >> 20) & 0x7)

    @property
    def height(self) -> int:
        return 8 << ((self.teximage_params >> 23) & 0x7)

    @property
    def format_id(self) -> int:
        return (self.teximage_params >> 26) & 0x7

    @property
    def color0_transparent(self) -> bool:
        return bool((self.teximage_params >> 29) & 0x1)


@dataclass(slots=True)
class PaletteEntry:
    name: str
    offset: int
    unknown: int


@dataclass(slots=True)
class Tex0Info:
    block1: bytes
    block2: bytes
    block3: bytes
    block4: bytes
    textures: list[TextureEntry]
    palettes: list[PaletteEntry]
    layout_name: str = ""


@dataclass(slots=True)
class TextureDecodeFailure:
    texture_name: str
    palette_hint: str | None
    format_id: int
    width: int
    height: int
    texture_offset: int
    layout_name: str
    reasons: list[str]


@dataclass(slots=True)
class GuidedDecodeReport:
    images: list[DecodedImage]
    failures: list[TextureDecodeFailure]
    candidate_summaries: list[str]
    selected_layout: str | None = None

