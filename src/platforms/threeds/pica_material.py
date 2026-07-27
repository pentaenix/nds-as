"""Decode the PICA200 output-merger state embedded in GF materials."""
from __future__ import annotations

from dataclasses import dataclass
import struct

from .pica import read_pica_commands

GPUREG_CULL_MODE = 0x0040
GPUREG_COLOR_OPERATION = 0x0100
GPUREG_BLEND_FUNC = 0x0101
GPUREG_FRAGOP_ALPHA_TEST = 0x0104
GPUREG_DEPTH_COLOR_MASK = 0x0107

_CULL_COMMAND_HEADER = struct.pack("<I", 0x000F0040)
_BLEND_FACTORS = (
    "zero",
    "one",
    "source_color",
    "one_minus_source_color",
    "destination_color",
    "one_minus_destination_color",
    "source_alpha",
    "one_minus_source_alpha",
    "destination_alpha",
    "one_minus_destination_alpha",
    "constant_color",
    "one_minus_constant_color",
    "constant_alpha",
    "one_minus_constant_alpha",
    "source_alpha_saturate",
)
_BLEND_EQUATIONS = ("add", "subtract", "reverse_subtract", "min", "max")
_COMPARE_FUNCTIONS = (
    "never",
    "always",
    "equal",
    "not_equal",
    "less",
    "less_or_equal",
    "greater",
    "greater_or_equal",
)


def _enum_name(values: tuple[str, ...], value: int) -> str:
    return values[value] if 0 <= value < len(values) else f"unknown_{value}"


@dataclass(frozen=True, slots=True)
class PicaRenderState:
    color_operation: int
    blend_function: int
    alpha_test: int
    depth_color_mask: int
    cull_mode: int | None = None

    @property
    def alpha_blend_enabled(self) -> bool:
        return bool(self.color_operation & (1 << 8))

    @property
    def source_rgb_factor(self) -> int:
        return (self.blend_function >> 16) & 0xF

    @property
    def destination_rgb_factor(self) -> int:
        return (self.blend_function >> 20) & 0xF

    @property
    def source_alpha_factor(self) -> int:
        return (self.blend_function >> 24) & 0xF

    @property
    def destination_alpha_factor(self) -> int:
        return (self.blend_function >> 28) & 0xF

    @property
    def alpha_test_enabled(self) -> bool:
        return bool(self.alpha_test & 1)

    @property
    def alpha_test_function(self) -> int:
        return (self.alpha_test >> 4) & 0x7

    @property
    def alpha_test_reference(self) -> int:
        return (self.alpha_test >> 8) & 0xFF

    @property
    def depth_write_enabled(self) -> bool:
        return bool(self.depth_color_mask & (1 << 12))

    @property
    def cull_backface_enabled(self) -> bool:
        return self.cull_mode == 2

    @property
    def cull_frontface_enabled(self) -> bool:
        return self.cull_mode == 1

    def render_class(self) -> str:
        if self.alpha_blend_enabled:
            source = self.source_rgb_factor
            destination = self.destination_rgb_factor
            if source == 1 and destination == 0:
                return "mask" if self.alpha_test_enabled else "opaque"
            if destination == 1 and source in (1, 6):
                return "additive"
            return "blend"
        return "mask" if self.alpha_test_enabled else "opaque"

    def to_extras(self) -> dict:
        blend = self.blend_function
        return {
            "colorOperationRaw": self.color_operation,
            "blendFunctionRaw": blend,
            "alphaTestRaw": self.alpha_test,
            "depthColorMaskRaw": self.depth_color_mask,
            "cullModeRaw": self.cull_mode,
            "cullBackface": self.cull_backface_enabled,
            "cullFrontface": self.cull_frontface_enabled,
            "alphaBlendEnabled": self.alpha_blend_enabled,
            "rgbEquation": _enum_name(_BLEND_EQUATIONS, blend & 0xFF),
            "alphaEquation": _enum_name(_BLEND_EQUATIONS, (blend >> 8) & 0xFF),
            "sourceRgbFactor": _enum_name(_BLEND_FACTORS, self.source_rgb_factor),
            "destinationRgbFactor": _enum_name(
                _BLEND_FACTORS, self.destination_rgb_factor
            ),
            "sourceAlphaFactor": _enum_name(_BLEND_FACTORS, self.source_alpha_factor),
            "destinationAlphaFactor": _enum_name(
                _BLEND_FACTORS, self.destination_alpha_factor
            ),
            "alphaTestEnabled": self.alpha_test_enabled,
            "alphaTestFunction": _enum_name(
                _COMPARE_FUNCTIONS, self.alpha_test_function
            ),
            "alphaTestReference": self.alpha_test_reference / 255.0,
            "depthWriteEnabled": self.depth_write_enabled,
        }


def parse_pica_render_state(data: bytes) -> PicaRenderState | None:
    """Find and decode the output-merger command list in a GF material tail.

    GF material metadata before the command list has variable-length strings.
    The cull command is the stable first output-merger command; anchoring on its
    exact header avoids treating adjacent parameter words as command headers.
    """
    registers = parse_pica_registers(data)
    if registers is None:
        return None
    if all(
        register in registers
        for register in (
            GPUREG_COLOR_OPERATION,
            GPUREG_BLEND_FUNC,
            GPUREG_FRAGOP_ALPHA_TEST,
            GPUREG_DEPTH_COLOR_MASK,
        )
    ):
        return PicaRenderState(
            color_operation=registers[GPUREG_COLOR_OPERATION],
            blend_function=registers[GPUREG_BLEND_FUNC],
            alpha_test=registers[GPUREG_FRAGOP_ALPHA_TEST],
            depth_color_mask=registers[GPUREG_DEPTH_COLOR_MASK],
            cull_mode=registers.get(GPUREG_CULL_MODE),
        )
    return None


def parse_pica_registers(data: bytes) -> dict[int, int] | None:
    """Return the full PICA command register map from a GF material tail."""
    search_from = 0
    while True:
        header_at = data.find(_CULL_COMMAND_HEADER, search_from)
        if header_at < 0:
            return None
        if header_at < 4:
            search_from = header_at + 4
            continue
        start = header_at - 4
        usable = len(data) - start
        words = list(struct.unpack_from(f"<{usable // 4}I", data, start))
        registers: dict[int, int] = {}
        for register, parameter in read_pica_commands(words):
            registers[register] = parameter
        if registers:
            return registers
        search_from = header_at + 4
