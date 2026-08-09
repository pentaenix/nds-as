"""NDS BGR555 palette and color helpers."""
from __future__ import annotations

def mix(c0: tuple[int, int, int, int], c1: tuple[int, int, int, int], a: int, b: int, denom: int) -> tuple[int, int, int, int]:
    return (
        (c0[0] * a + c1[0] * b) // denom,
        (c0[1] * a + c1[1] * b) // denom,
        (c0[2] * a + c1[2] * b) // denom,
        (c0[3] * a + c1[3] * b) // denom,
    )

def read_palette(block4: bytes, offset: int, count: int) -> list[tuple[int, int, int, int]]:
    colors: list[tuple[int, int, int, int]] = []
    start = offset
    for i in range(count):
        pos = start + i * 2
        if pos + 2 > len(block4):
            break
        colors.append(bgr555_to_rgba(int.from_bytes(block4[pos:pos + 2], "little")))
    return colors


def bgr555_to_rgba(value: int, *, alpha_bit: bool = False) -> tuple[int, int, int, int]:
    r5 = value & 0x1F
    g5 = (value >> 5) & 0x1F
    b5 = (value >> 10) & 0x1F
    a = 255
    if alpha_bit and not (value & 0x8000):
        a = 0
    return ((r5 << 3) | (r5 >> 2), (g5 << 3) | (g5 >> 2), (b5 << 3) | (b5 >> 2), a)


def apply_color0(color: tuple[int, int, int, int], idx: int, color0_transparent: bool) -> tuple[int, int, int, int]:
    if idx == 0 and color0_transparent:
        return (color[0], color[1], color[2], 0)
    return color

