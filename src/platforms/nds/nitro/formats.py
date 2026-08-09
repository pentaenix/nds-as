"""Per-format Nitro texture decoders (indexed, direct, 4x4)."""
from __future__ import annotations

from ....core.util import read_u16le, read_u32le
from .color import apply_color0, bgr555_to_rgba, mix, read_palette
from .palette_match import palette_candidates_for_4x4
from .types import DecodedImage, PaletteEntry, Tex0Info, TextureEntry

def decode_texture(texture: TextureEntry, palette: PaletteEntry | None, tex0: Tex0Info) -> DecodedImage | None:
    fmt = texture.format_id
    w, h = texture.width, texture.height
    if w <= 0 or h <= 0 or w > 4096 or h > 4096:
        return None
    if fmt == 5:
        return decode_texture_4x4(texture, palette, tex0)

    if fmt == 7:
        needed = w * h * 2
        raw = tex0.block1[texture.offset:texture.offset + needed]
        if len(raw) < needed:
            return None
        rgba = bytearray()
        for i in range(0, needed, 2):
            color = int.from_bytes(raw[i:i + 2], "little")
            r, g, b, a = bgr555_to_rgba(color, alpha_bit=True)
            rgba += bytes((r, g, b, a))
        return DecodedImage(texture.name, w, h, bytes(rgba), "BTX0 direct color", fmt, None)

    if palette is None:
        return None
    pal_count = {1: 32, 2: 4, 3: 16, 4: 256, 6: 8}.get(fmt)
    if pal_count is None:
        return None
    palette_colors = read_palette(tex0.block4, palette.offset, pal_count)
    if not palette_colors:
        return None

    rgba = bytearray()
    if fmt == 1:  # A3I5
        raw = tex0.block1[texture.offset:texture.offset + w * h]
        if len(raw) < w * h:
            return None
        for value in raw[:w * h]:
            idx = value & 0x1F
            alpha = ((value >> 5) & 0x7) * 255 // 7
            r, g, b, _ = palette_colors[idx % len(palette_colors)]
            rgba += bytes((r, g, b, alpha))
    elif fmt == 2:  # 2bpp indexed
        raw = tex0.block1[texture.offset:texture.offset + ((w * h + 3) // 4)]
        pixels = []
        for byte in raw:
            pixels.extend([(byte >> shift) & 0x3 for shift in (0, 2, 4, 6)])
        for idx in pixels[:w * h]:
            rgba += bytes(apply_color0(palette_colors[idx % len(palette_colors)], idx, texture.color0_transparent))
    elif fmt == 3:  # 4bpp indexed
        raw = tex0.block1[texture.offset:texture.offset + ((w * h + 1) // 2)]
        pixels = []
        for byte in raw:
            pixels.extend([byte & 0xF, (byte >> 4) & 0xF])
        for idx in pixels[:w * h]:
            rgba += bytes(apply_color0(palette_colors[idx % len(palette_colors)], idx, texture.color0_transparent))
    elif fmt == 4:  # 8bpp indexed
        raw = tex0.block1[texture.offset:texture.offset + w * h]
        if len(raw) < w * h:
            return None
        for idx in raw[:w * h]:
            rgba += bytes(apply_color0(palette_colors[idx % len(palette_colors)], idx, texture.color0_transparent))
    elif fmt == 6:  # A5I3
        raw = tex0.block1[texture.offset:texture.offset + w * h]
        if len(raw) < w * h:
            return None
        for value in raw[:w * h]:
            idx = value & 0x7
            alpha = ((value >> 3) & 0x1F) * 255 // 31
            r, g, b, _ = palette_colors[idx % len(palette_colors)]
            rgba += bytes((r, g, b, alpha))
    else:
        return None

    return DecodedImage(texture.name, w, h, bytes(rgba), "BTX0 indexed", fmt, palette.name)

def decode_texture_4x4(texture: TextureEntry, palette: PaletteEntry | None, tex0: Tex0Info) -> DecodedImage | None:
    w, h = texture.width, texture.height
    if w <= 0 or h <= 0 or w > 2048 or h > 2048:
        return None
    blocks_x = (w + 3) // 4
    blocks_y = (h + 3) // 4
    block_count = blocks_x * blocks_y
    data_off = texture.offset
    texels = tex0.block2[data_off:data_off + block_count * 4]
    indices = tex0.block3[(data_off // 2):(data_off // 2) + block_count * 2]
    if len(texels) < block_count * 4 or len(indices) < block_count * 2:
        return None

    rgba = bytearray([0, 0, 0, 0] * (w * h))
    decoded_any_block = False
    for by in range(blocks_y):
        for bx in range(blocks_x):
            bi = by * blocks_x + bx
            bits = read_u32le(texels, bi * 4)
            pal_word = read_u16le(indices, bi * 2)
            mode = (pal_word >> 14) & 0x3

            colors = None
            for pal_off in palette_candidates_for_4x4(tex0, palette, pal_word):
                base = read_palette(tex0.block4, pal_off, 4)
                if len(base) >= 2:
                    if mode == 0:
                        colors = [base[0], base[1], base[2] if len(base) > 2 else base[0], (0, 0, 0, 0)]
                    elif mode == 1:
                        colors = [base[0], base[1], mix(base[0], base[1], 1, 1, 2), (0, 0, 0, 0)]
                    elif mode == 2:
                        colors = [base[0], base[1], base[2] if len(base) > 2 else base[0], base[3] if len(base) > 3 else base[1]]
                    else:  # mode 3
                        colors = [base[0], base[1], mix(base[0], base[1], 5, 3, 8), mix(base[0], base[1], 3, 5, 8)]
                    break
            if colors is None:
                continue
            decoded_any_block = True

            for py in range(4):
                for px in range(4):
                    dst_x = bx * 4 + px
                    dst_y = by * 4 + py
                    if dst_x >= w or dst_y >= h:
                        continue
                    idx = (bits >> ((py * 4 + px) * 2)) & 0x3
                    off = (dst_y * w + dst_x) * 4
                    rgba[off:off + 4] = bytes(colors[idx])
    if not decoded_any_block:
        return None
    return DecodedImage(texture.name, w, h, bytes(rgba), "BTX0 4x4 compressed", 5, palette.name if palette else None)

