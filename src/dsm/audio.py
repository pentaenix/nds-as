from __future__ import annotations

import json
import shutil
import struct
import subprocess
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .util import read_u16le, read_u32le, sanitize_component

AUDIO_MAGICS: dict[bytes, tuple[str, str]] = {
    b"SDAT": ("Sound archive", ".sdat"),
    b"SSEQ": ("Sound sequence", ".sseq"),
    b"SSAR": ("Sound sequence archive / SFX", ".ssar"),
    b"SBNK": ("Sound bank / instruments", ".sbnk"),
    b"SWAR": ("Sound wave archive", ".swar"),
    b"SWAV": ("Sound wave sample", ".swav"),
    b"STRM": ("Sound stream", ".strm"),
}

AUDIO_EXTENSIONS = {ext for _kind, ext in AUDIO_MAGICS.values()}


@dataclass(slots=True)
class AudioEntry:
    path: str
    data: bytes
    offset: int
    size: int
    magic: str
    index: int


def iter_nitro_sections(data: bytes):
    """Yield Nitro-standard file sections as (magic, absolute_start, payload_start, size).

    SDAT/SWAV/STRM/SWAR all use the common Nitro file header shape: magic, endian,
    version, total size, header size, section count, followed by section blocks.
    """
    if len(data) < 0x10:
        return
    header_size = read_u16le(data, 0x0C)
    section_count = read_u16le(data, 0x0E)
    pos = header_size if 0x10 <= header_size < len(data) else 0x10
    for _ in range(min(section_count or 8, 64)):
        if pos + 8 > len(data):
            break
        magic = data[pos:pos + 4]
        size = read_u32le(data, pos + 4)
        if size < 8 or pos + size > len(data):
            break
        yield magic, pos, pos + 8, size
        pos += size
        if pos & 3:
            pos = (pos + 3) & ~3


def find_section(data: bytes, names: set[bytes]) -> tuple[int, bytes] | None:
    for magic, start, payload_start, size in iter_nitro_sections(data) or []:
        if magic in names:
            return payload_start, data[payload_start:start + size]
    return None


def iter_sdat_files(data: bytes) -> list[AudioEntry]:
    """Extract subfiles from an SDAT by using the FAT and FILE blocks.

    This intentionally avoids interpreting SYMB/INFO relationship tables for now;
    it gives DSM lossless child extraction for SSEQ/SSAR/SBNK/SWAR/SWAV/STRM and
    leaves semantic pairing to mappings/metadata.
    """
    if not data.startswith(b"SDAT"):
        return []
    fat = find_section(data, {b"FAT ", b"FAT"})
    file_block = find_section(data, {b"FILE"})
    if not fat or not file_block:
        return []
    _fat_start, fat_payload = fat
    file_payload_start, file_payload = file_block
    if len(fat_payload) < 4:
        return []
    count = read_u32le(fat_payload, 0)
    entries: list[AudioEntry] = []
    for i in range(min(count, 100000)):
        off = 4 + i * 16
        if off + 8 > len(fat_payload):
            break
        rel = read_u32le(fat_payload, off)
        size = read_u32le(fat_payload, off + 4)
        if size <= 0:
            continue
        # In the common SDAT layout, FAT offsets are relative to the beginning of
        # the FILE section payload. Some older docs/tools describe them as file-
        # relative; try payload-relative first, then absolute as a fallback.
        candidates = [file_payload_start + rel, rel]
        start = -1
        for candidate in candidates:
            if 0 <= candidate <= len(data) and candidate + size <= len(data):
                start = candidate
                break
        if start < 0:
            continue
        sub = data[start:start + size]
        magic = sub[:4].decode("ascii", errors="replace") if len(sub) >= 4 else "BIN"
        ext = AUDIO_MAGICS.get(sub[:4], ("Binary", ".bin"))[1]
        entries.append(AudioEntry(f"sdat_{i:04d}_{magic.lower()}{ext}", sub, start, size, magic, i))
    return entries


def iter_swar_swavs(data: bytes) -> list[AudioEntry]:
    if not data.startswith(b"SWAR"):
        return []
    data_sec = find_section(data, {b"DATA"})
    if not data_sec:
        return []
    data_payload_start, payload = data_sec
    # SWAR DATA commonly starts with a u32 count, then u32 offsets. The offsets
    # usually point into the SWAR file, but some docs/tools treat them relative
    # to DATA. We validate each candidate before trusting it.
    if len(payload) < 4:
        return []
    count = read_u32le(payload, 0)
    if count <= 0 or count > 100000 or 4 + count * 4 > len(payload):
        return []
    offsets = [read_u32le(payload, 4 + i * 4) for i in range(count)]
    entries: list[AudioEntry] = []
    valid_starts: list[int] = []
    for off in offsets:
        for candidate in (off, data_payload_start + off, data_payload_start + off - 8):
            if 0 <= candidate < len(data) and data[candidate:candidate + 4] == b"SWAV":
                valid_starts.append(candidate)
                break
    if not valid_starts:
        return []
    sorted_starts = sorted((s, idx) for idx, s in enumerate(valid_starts))
    for n, (start, orig_idx) in enumerate(sorted_starts):
        end = sorted_starts[n + 1][0] if n + 1 < len(sorted_starts) else len(data)
        size_from_header = read_u32le(data, start + 8) if start + 12 <= len(data) else 0
        if size_from_header and start + size_from_header <= len(data):
            end = start + size_from_header
        sub = data[start:end]
        entries.append(AudioEntry(f"swar_{orig_idx:04d}.swav", sub, start, len(sub), "SWAV", orig_idx))
    return entries


def export_audio_bundle(asset, out_dir: str | Path, *, make_mp3: bool = False) -> list[Path]:
    """Export lossless audio pieces and WAV previews where DSM can decode them.

    WAV is the best-quality practical output for PCM/sample previews because it is
    lossless. MP3 is optional and only created when ffmpeg is available.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    stem = sanitize_component(Path(asset.virtual_path).stem or asset.asset_id)
    bundle = out / f"{stem}_{asset.asset_id}"
    bundle.mkdir(parents=True, exist_ok=True)

    raw_path = bundle / f"raw{asset.extension or '.bin'}"
    raw_path.write_bytes(asset.data)
    written.append(raw_path)

    manifest = {
        "sourcePath": asset.virtual_path,
        "magic": asset.magic,
        "kind": asset.kind,
        "notes": [],
        "children": [],
    }

    if asset.magic == "SDAT":
        children = iter_sdat_files(asset.data)
        for child in children:
            child_dir = bundle / "sdat_files"
            child_dir.mkdir(exist_ok=True)
            child_name = sanitize_component(child.path)
            path = child_dir / child_name
            path.write_bytes(child.data)
            written.append(path)
            child_record = {
                "index": child.index,
                "path": child.path,
                "magic": child.magic,
                "offset": child.offset,
                "size": child.size,
                "exported": str(path.relative_to(bundle)),
            }
            if child.magic == "SWAR":
                swavs = iter_swar_swavs(child.data)
                swav_dir = child_dir / f"{Path(child_name).stem}_swav"
                for swav in swavs:
                    swav_dir.mkdir(exist_ok=True)
                    swav_path = swav_dir / sanitize_component(swav.path)
                    swav_path.write_bytes(swav.data)
                    written.append(swav_path)
                    wav = try_write_wav(swav.data, swav_dir / (swav_path.stem + ".wav"))
                    if wav:
                        written.append(wav)
                child_record["swavCount"] = len(swavs)
            elif child.magic in {"SWAV", "STRM"}:
                wav = try_write_wav(child.data, child_dir / (Path(child_name).stem + ".wav"))
                if wav:
                    written.append(wav)
                    child_record["wav"] = str(wav.relative_to(bundle))
            manifest["children"].append(child_record)
        manifest["notes"].append("SSEQ/SSAR are sequenced audio, not PCM. DSM exports them losslessly as raw files; use VGMTrans/Nitro Studio for MIDI/SF2-style conversion.")
    elif asset.magic == "SWAR":
        swavs = iter_swar_swavs(asset.data)
        for swav in swavs:
            swav_path = bundle / sanitize_component(swav.path)
            swav_path.write_bytes(swav.data)
            written.append(swav_path)
            wav = try_write_wav(swav.data, bundle / (swav_path.stem + ".wav"))
            if wav:
                written.append(wav)
        manifest["children"].append({"swavCount": len(swavs)})
    elif asset.magic in {"SWAV", "STRM"}:
        wav = try_write_wav(asset.data, bundle / f"{stem}.wav")
        if wav:
            written.append(wav)
        else:
            manifest["notes"].append("DSM could not safely decode this audio payload yet; raw original was exported losslessly.")
    else:
        manifest["notes"].append("Sequenced/instrument audio was exported raw losslessly. Decode/render externally with VGMTrans or Nitro Studio when needed.")

    if make_mp3:
        mp3_paths = write_mp3_copies([p for p in written if p.suffix.lower() == ".wav"], bundle)
        written.extend(mp3_paths)
        if mp3_paths:
            manifest["mp3"] = [str(p.relative_to(bundle)) for p in mp3_paths]
        else:
            manifest["notes"].append("MP3 requested, but ffmpeg was not available or conversion failed. WAV previews are lossless and preferred for quality.")

    manifest_path = bundle / "audio_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    written.append(manifest_path)
    return written


def write_mp3_copies(wav_paths: list[Path], bundle: Path) -> list[Path]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return []
    out_dir = bundle / "mp3"
    out_dir.mkdir(exist_ok=True)
    written: list[Path] = []
    for wav_path in wav_paths:
        mp3 = out_dir / (wav_path.stem + ".mp3")
        cmd = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(wav_path), "-codec:a", "libmp3lame", "-q:a", "0", str(mp3)]
        try:
            proc = subprocess.run(cmd, text=True, capture_output=True)
        except Exception:
            continue
        if proc.returncode == 0 and mp3.exists():
            written.append(mp3)
    return written


def try_write_wav(data: bytes, out_path: str | Path) -> Path | None:
    if data.startswith(b"SWAV"):
        pcm = decode_swav_pcm(data)
    elif data.startswith(b"STRM"):
        pcm = decode_strm_pcm(data)
    else:
        pcm = None
    if pcm is None:
        return None
    sample_rate, channels, sample_width, pcm_bytes = pcm
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return out


def decode_swav_pcm(data: bytes) -> tuple[int, int, int, bytes] | None:
    data_sec = find_section(data, {b"DATA"})
    if not data_sec:
        return None
    _payload_start, payload = data_sec
    if len(payload) < 12:
        return None
    wave_type = payload[0]
    sample_rate = read_u16le(payload, 2) or 32768
    # Common SWAV DATA header is 12 bytes after section payload start.
    raw = payload[12:]
    if not raw:
        return None
    if wave_type == 0:  # unsigned 8-bit PCM on DS; WAV wants unsigned 8-bit too.
        return sample_rate, 1, 1, raw
    if wave_type == 1:  # signed little-endian 16-bit PCM.
        return sample_rate, 1, 2, raw[:len(raw) & ~1]
    if wave_type == 2:  # IMA-ADPCM-ish DS nibble stream.
        decoded = decode_ds_ima_adpcm(raw)
        return sample_rate, 1, 2, decoded
    return None


def decode_strm_pcm(data: bytes) -> tuple[int, int, int, bytes] | None:
    # STRM layouts vary; this conservative decoder handles simple PCM8/PCM16
    # streams when the standard HEAD/DATA fields are present. ADPCM and blocks are
    # left raw until we can test against real Pokémon samples.
    head = find_section(data, {b"HEAD"})
    data_sec = find_section(data, {b"DATA"})
    if not head or not data_sec:
        return None
    _head_start, h = head
    _data_start, payload = data_sec
    if len(h) < 0x10:
        return None
    wave_type = h[0]
    channels = max(1, min(2, h[1] if h[1] else 1))
    sample_rate = read_u16le(h, 2) or 32768
    raw = payload
    # Many STRM DATA blocks begin with a small header/reserved area; if the first
    # 4 bytes look like a size, skip 8 bytes. This is intentionally cautious.
    if len(raw) > 8 and read_u32le(raw, 0) in {0, len(raw) - 8, len(raw)}:
        raw = raw[8:]
    if wave_type == 0:
        return sample_rate, channels, 1, raw
    if wave_type == 1:
        return sample_rate, channels, 2, raw[:len(raw) & ~1]
    return None


def decode_ds_ima_adpcm(raw: bytes) -> bytes:
    # Nintendo DS SWAV ADPCM is close enough to IMA ADPCM for useful preview WAVs.
    # This decoder favors safe, audible extraction over edit-roundtrip accuracy.
    index_table = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8]
    step_table = [
        7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31,
        34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130,
        143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449,
        494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
        1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026,
        4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
        11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623,
        27086, 29794, 32767,
    ]
    if len(raw) < 4:
        return b""
    predictor = struct.unpack_from("<h", raw, 0)[0]
    index = max(0, min(88, raw[2]))
    out = bytearray(struct.pack("<h", predictor))
    for byte in raw[4:]:
        for nibble in (byte & 0x0F, byte >> 4):
            step = step_table[index]
            diff = step >> 3
            if nibble & 1:
                diff += step >> 2
            if nibble & 2:
                diff += step >> 1
            if nibble & 4:
                diff += step
            predictor = predictor - diff if nibble & 8 else predictor + diff
            predictor = max(-32768, min(32767, predictor))
            index = max(0, min(88, index + index_table[nibble]))
            out.extend(struct.pack("<h", predictor))
    return bytes(out)
