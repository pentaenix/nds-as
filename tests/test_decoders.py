import struct
from pathlib import Path

from rae.nitro_textures import decode_btx_images
from rae.scanner import scan_nds_path
from test_synthetic_scan import make_nds


def namelist(entries, entry_size):
    count = len(entries)
    data = b''.join(payload.ljust(entry_size, b'\0')[:entry_size] for _name, payload in entries)
    names = b''.join(name.encode('ascii')[:16].ljust(16, b'\0') for name, _payload in entries)
    total = 4 + 8 + count * 4 + 4 + len(data) + len(names)
    return (
        struct.pack('<BBH', 0, count, total)
        + struct.pack('<HHI', 8, 8 + count * 4, 0)
        + b'\0\0\0\0' * count
        + struct.pack('<HH', entry_size, len(data))
        + data
        + names
    )


def make_btx0_4bpp():
    # 8x8, 4bpp indexed. Low nibble then high nibble per byte.
    texels = bytes(((i % 16) | (((i + 1) % 16) << 4)) for i in range(0, 64, 2))
    palette = b''.join(struct.pack('<H', i | (i << 5) | (i << 10)) for i in range(16))
    tex_params = (0 << 0) | (0 << 20) | (0 << 23) | (3 << 26)
    textures = namelist([('boat_tex', struct.pack('<II', tex_params, 0))], 8)
    palettes = namelist([('boat_tex', struct.pack('<HH', 0, 0))], 4)

    header_len = 0x38
    textures_off = header_len
    palettes_off = textures_off + len(textures)
    block1_off = palettes_off + len(palettes)
    block4_off = block1_off + len(texels)
    tex0 = bytearray(b'TEX0' + b'\0' * (header_len - 4))
    struct.pack_into('<H', tex0, 0x0C, len(texels) >> 3)
    struct.pack_into('<H', tex0, 0x0E, textures_off)
    struct.pack_into('<I', tex0, 0x14, block1_off)
    struct.pack_into('<H', tex0, 0x18, 0)
    struct.pack_into('<I', tex0, 0x20, 0)
    struct.pack_into('<I', tex0, 0x24, 0)
    struct.pack_into('<H', tex0, 0x2C, len(palette) >> 3)
    struct.pack_into('<I', tex0, 0x30, palettes_off)
    struct.pack_into('<I', tex0, 0x34, block4_off)
    tex0 = bytes(tex0) + textures + palettes + texels + palette

    total = 0x14 + len(tex0)
    btx = bytearray(b'BTX0' + struct.pack('<HHIHHI', 0xFFFE, 0x0100, total, 0x10, 1, 0x14))
    return bytes(btx) + tex0


def test_decodes_synthetic_btx0_texture():
    images = decode_btx_images(make_btx0_4bpp())
    assert len(images) == 1
    assert images[0].name == 'boat_tex'
    assert images[0].width == 8
    assert images[0].height == 8
    assert len(images[0].rgba) == 8 * 8 * 4


def test_scanner_identifies_2d_palette_and_png(tmp_path: Path):
    fake_nclr = b'RLCN' + b'\0' * 20
    fake_png = b'\x89PNG\r\n\x1a\n' + b'\0' * 16
    rom = make_nds({'pal.bin': fake_nclr, 'image.bin': fake_png})
    rom_path = tmp_path / 'assets.nds'
    rom_path.write_bytes(rom)
    assets = scan_nds_path(str(rom_path), carve_unknown_blobs=False)
    assert [a.magic for a in assets] == ['RLCN', 'PNG']


def test_nitro_name_filter_rejects_repetitive_false_positives():
    from rae.nitro_names import extract_nitro_names

    blob = b"DDDD\x00".ljust(16, b"\x00") + b"wwww\x00".ljust(16, b"\x00") + b"kabe_pl\x00".ljust(16, b"\x00") + b"light01\x00".ljust(16, b"\x00")
    names = extract_nitro_names(blob)
    assert "DDDD" not in names
    assert "wwww" not in names
    assert "kabe_pl" in names
    assert "light01" in names
