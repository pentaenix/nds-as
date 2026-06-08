"""Nitro BTX0/TEX0 texture decoding package."""
from .color import apply_color0, bgr555_to_rgba
from .decode import (
    attempt_decode_texture,
    decode_btx_images,
    decode_guided_tex0_images,
    decode_guided_tex0_report,
)
from .palette_match import palette_options_for_texture
from .export import make_contact_sheet, save_decoded_images
from .parse import parse_tex0, parse_tex0_manifest
from .tex0_layout import find_tex0_offset, parse_tex0_candidates, prepare_tex0
from .tex0_namelist import parse_namelist
from .types import (
    DecodedImage,
    GuidedDecodeReport,
    NitroNameEntry,
    PaletteEntry,
    Tex0Info,
    TextureDecodeFailure,
    TextureEntry,
)
from .validate import (
    format_decode_failure,
    score_tex0_candidate,
    score_tex0_candidate_structural,
    texture_data_requirements,
    validate_texture_ranges,
)

__all__ = [
    "DecodedImage",
    "GuidedDecodeReport",
    "NitroNameEntry",
    "PaletteEntry",
    "Tex0Info",
    "TextureDecodeFailure",
    "TextureEntry",
    "apply_color0",
    "attempt_decode_texture",
    "bgr555_to_rgba",
    "decode_btx_images",
    "decode_guided_tex0_images",
    "decode_guided_tex0_report",
    "find_tex0_offset",
    "format_decode_failure",
    "make_contact_sheet",
    "parse_namelist",
    "parse_tex0",
    "parse_tex0_candidates",
    "parse_tex0_manifest",
    "palette_options_for_texture",
    "prepare_tex0",
    "save_decoded_images",
    "score_tex0_candidate",
    "score_tex0_candidate_structural",
    "texture_data_requirements",
    "validate_texture_ranges",
]
