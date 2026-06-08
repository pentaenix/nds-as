"""TEX0 block layout parsing and candidate discovery."""
from __future__ import annotations

from ....core.util import read_u16le, read_u32le
from .tex0_namelist import parse_namelist
from .types import PaletteEntry, Tex0Info, TextureEntry

try:
    from .compression import decompress_lz10, looks_like_lz10
except Exception:  # pragma: no cover
    def looks_like_lz10(_data: bytes) -> bool:
        return False

    def decompress_lz10(data: bytes) -> bytes:
        raise ValueError("LZ10 unavailable")


def parse_tex0_candidates(data: bytes) -> list[Tex0Info]:
    """Return every plausible TEX0 layout candidate found in a blob."""
    tex_off = find_tex0_offset(data)
    if tex_off is None or tex_off + 0x38 > len(data):
        return []
    if data[tex_off:tex_off + 4] != b"TEX0":
        return []

    candidates: list[Tex0Info] = []
    layouts = [
        ("apicula", 0x1C, 0x24, 0x28, 0x30, True, 0x34, 0x38),
        ("scurest", 0x18, 0x20, 0x24, 0x2C, False, 0x30, 0x34),
        ("vgresource", 0x1C, 0x24, 0x28, 0x30, True, 0x34, 0x38),
    ]
    for layout_name, block2_len_pos, block2_off_pos, block3_off_pos, block4_len_pos, block4_len_u32, palettes_off_pos, block4_off_pos in layouts:
        info = _parse_tex0_layout(
            data,
            tex_off,
            block2_len_pos,
            block2_off_pos,
            block3_off_pos,
            block4_len_pos,
            block4_len_u32,
            palettes_off_pos,
            block4_off_pos,
            layout_name=layout_name,
        )
        if info is not None:
            candidates.append(info)
    return candidates

def _tex0_data_end(data: bytes, tex_off: int) -> int:
    end = len(data)
    if tex_off > 0 and len(data) >= 0x0C and data[:4] in {b"BMD0", b"BTX0"}:
        container_size = read_u32le(data, 0x08)
        if container_size > tex_off:
            end = min(end, container_size)
    return end


def _rel_slice_between(data: bytes, tex_off: int, start_rel: int, end_rel: int, fallback_len: int) -> bytes:
    if start_rel > 0 and end_rel > start_rel:
        start = tex_off + start_rel
        end = tex_off + end_rel
        if 0 <= start < len(data) and end <= len(data) and end > start:
            return data[start:end]
    if start_rel > 0:
        start = tex_off + start_rel
        end = min(start + max(0, fallback_len), len(data))
        if start < len(data) and end > start:
            return data[start:end]
    return b""


def _parse_tex0_layout(
    data: bytes,
    tex_off: int,
    block2_len_pos: int,
    block2_off_pos: int,
    block3_off_pos: int,
    block4_len_pos: int,
    block4_len_u32: bool,
    palettes_off_pos: int,
    block4_off_pos: int,
    *,
    layout_name: str = "",
) -> Tex0Info | None:
    try:
        block1_len = read_u16le(data, tex_off + 0x0C) << 3
        textures_off = read_u16le(data, tex_off + 0x0E)
        block1_off = read_u32le(data, tex_off + 0x14)
        block2_len = read_u16le(data, tex_off + block2_len_pos) << 3
        block2_off = read_u32le(data, tex_off + block2_off_pos)
        block3_off = read_u32le(data, tex_off + block3_off_pos)
        if block4_len_u32:
            raw_len = read_u32le(data, tex_off + block4_len_pos)
            block4_len = raw_len << 3 if raw_len < (1 << 24) else 0
        else:
            block4_len = read_u16le(data, tex_off + block4_len_pos) << 3
        palettes_off = read_u32le(data, tex_off + palettes_off_pos)
        block4_off = read_u32le(data, tex_off + block4_off_pos)
    except Exception:
        return None

    tex0_end_rel = _tex0_data_end(data, tex_off) - tex_off

    def slice_block(start_rel: int, next_rel: int, fallback_len: int) -> bytes:
        if start_rel > 0 and next_rel > start_rel:
            return _rel_slice_between(data, tex_off, start_rel, next_rel, 0)
        if start_rel > 0 and tex0_end_rel > start_rel and next_rel <= 0:
            return _rel_slice_between(data, tex_off, start_rel, tex0_end_rel, 0)
        return _rel_slice_between(data, tex_off, start_rel, 0, fallback_len)

    if textures_off <= 0 or tex_off + textures_off >= len(data):
        return None
    if palettes_off <= 0 or tex_off + palettes_off >= len(data):
        return None

    block1 = slice_block(block1_off, block2_off, block1_len)
    block2 = slice_block(block2_off, block3_off, block2_len)
    block3 = slice_block(block3_off, block4_off, max(block2_len // 2, 0))
    block4 = slice_block(block4_off, tex0_end_rel, block4_len)

    textures: list[TextureEntry] = []
    for entry in parse_namelist(data, tex_off + textures_off, 8):
        if len(entry.data) >= 8:
            params = read_u32le(entry.data, 0)
            tex = TextureEntry(entry.name, params, read_u32le(entry.data, 4))
            if 0 < tex.width <= 4096 and 0 < tex.height <= 4096 and 1 <= tex.format_id <= 7:
                textures.append(tex)

    palettes: list[PaletteEntry] = []
    for entry in parse_namelist(data, tex_off + palettes_off, 4):
        if len(entry.data) >= 4:
            pal = PaletteEntry(entry.name, read_u16le(entry.data, 0) << 3, read_u16le(entry.data, 2))
            if 0 <= pal.offset < max(len(block4), 1 << 30):
                palettes.append(pal)

    if not textures:
        return None
    return Tex0Info(
        block1=block1,
        block2=block2,
        block3=block3,
        block4=block4,
        textures=textures,
        palettes=palettes,
        layout_name=layout_name,
    )

def find_tex0_offset(data: bytes) -> int | None:
    if len(data) < 4:
        return None
    if data[:4] == b"TEX0":
        return 0
    if data[:4] in {b"BTX0", b"BMD0"} and len(data) >= 0x14:
        header_size = read_u16le(data, 0x0C)
        subfiles = read_u16le(data, 0x0E)
        table_start = 0x10
        for i in range(min(subfiles, 16)):
            off_pos = table_start + i * 4
            if off_pos + 4 <= len(data):
                off = read_u32le(data, off_pos)
                if 0 <= off <= len(data) - 4 and data[off:off + 4] == b"TEX0":
                    return off
        if 0 < header_size < len(data) and data[header_size:header_size + 4] == b"TEX0":
            return header_size
    pos = data.find(b"TEX0")
    return pos if pos >= 0 else None

def _maybe_decompress_block(data: bytes) -> bytes:
    if not data or not looks_like_lz10(data):
        return data
    try:
        return decompress_lz10(data)
    except Exception:
        return data


def prepare_tex0(tex0: Tex0Info) -> Tex0Info:
    """Return a TEX0 view with LZ10-compressed image/palette blocks expanded when possible."""
    block1 = _maybe_decompress_block(tex0.block1)
    block2 = _maybe_decompress_block(tex0.block2)
    block4 = _maybe_decompress_block(tex0.block4)
    if block1 == tex0.block1 and block2 == tex0.block2 and block4 == tex0.block4:
        return tex0
    return Tex0Info(
        block1=block1,
        block2=block2,
        block3=tex0.block3,
        block4=block4,
        textures=tex0.textures,
        palettes=tex0.palettes,
        layout_name=tex0.layout_name,
    )

