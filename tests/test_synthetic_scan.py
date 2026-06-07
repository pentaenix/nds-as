import struct
from pathlib import Path

from dsm.scanner import scan_nds_path


def make_narc(files):
    btaf_body = struct.pack('<HH', len(files), 0)
    gmif_body = b''
    pos = 0
    for payload in files:
        start = pos
        gmif_body += payload
        end = start + len(payload)
        gmif_body += b'\0' * ((-len(gmif_body)) % 4)
        pos = len(gmif_body)
        btaf_body += struct.pack('<II', start, end)
    btaf = b'BTAF' + struct.pack('<I', 8 + len(btaf_body)) + btaf_body
    gmif = b'GMIF' + struct.pack('<I', 8 + len(gmif_body)) + gmif_body
    total = 0x10 + len(btaf) + len(gmif)
    return b'NARC' + struct.pack('<HHIHH', 0xFFFE, 0x0100, total, 0x10, 2) + btaf + gmif


def make_nds(files):
    header = bytearray(0x200)
    header[0:12] = b'TESTGAME\0\0\0\0'
    header[0x0C:0x10] = b'TEST'

    fnt_off = 0x200
    sub = bytearray()
    for name in files:
        raw = name.encode('ascii')
        sub.append(len(raw))
        sub += raw
    sub.append(0)
    fnt = struct.pack('<IHH', 8, 0, 1) + sub

    fat_off = fnt_off + len(fnt)
    fat_off = (fat_off + 3) & ~3
    data_off = fat_off + len(files) * 8
    data_off = (data_off + 3) & ~3

    fat = bytearray()
    blob = bytearray()
    cur = data_off
    for payload in files.values():
        start = cur
        end = start + len(payload)
        fat += struct.pack('<II', start, end)
        blob += payload
        cur = end

    header[0x40:0x44] = struct.pack('<I', fnt_off)
    header[0x44:0x48] = struct.pack('<I', len(fnt))
    header[0x48:0x4C] = struct.pack('<I', fat_off)
    header[0x4C:0x50] = struct.pack('<I', len(fat))

    rom = bytearray(data_off)
    rom[:len(header)] = header
    rom[fnt_off:fnt_off + len(fnt)] = fnt
    rom[fat_off:fat_off + len(fat)] = fat
    rom += blob
    return bytes(rom)


def test_scans_loose_model_and_nested_narc_texture(tmp_path: Path):
    bmd = b'BMD0' + b'\0' * 16
    btx = b'BTX0' + b'\0' * 16
    rom = make_nds({'model.bin': bmd, 'archive.narc': make_narc([btx])})
    rom_path = tmp_path / 'test.nds'
    rom_path.write_bytes(rom)

    assets = scan_nds_path(str(rom_path))
    assert [a.magic for a in assets] == ['BMD0', 'BTX0']


def test_carves_embedded_nitro_model_from_unknown_container(tmp_path: Path):
    bmd = b'BMD0' + b'\xFE\xFF' + b'\x00\x01' + struct.pack('<IHH', 16, 16, 1)
    rom = make_nds({'custom_container.bin': b'WRAP' + b'\0' * 12 + bmd + b'TAIL'})
    rom_path = tmp_path / 'carve.nds'
    rom_path.write_bytes(rom)

    assets = scan_nds_path(str(rom_path))
    assert len(assets) == 1
    assert assets[0].magic == 'BMD0'
    assert assets[0].carved is True
    assert '#carved_' in assets[0].virtual_path


def test_initial_scan_does_not_expand_audio_archives_by_default(tmp_path: Path):
    # A deliberately small/fake SDAT should be listed as one audio archive, not
    # recursively expanded during normal UI load. Real child extraction happens
    # during explicit audio export, which prevents initial-load memory blowups.
    sdat = b'SDAT' + b'\xFE\xFF' + b'\x00\x01' + struct.pack('<IHH', 0x20, 0x10, 0) + b'\0' * 0x10
    rom = make_nds({'sound_data.sdat': sdat})
    rom_path = tmp_path / 'audio.nds'
    rom_path.write_bytes(rom)

    assets = scan_nds_path(str(rom_path), carve_unknown_blobs=False)
    assert len(assets) == 1
    assert assets[0].magic == 'SDAT'
