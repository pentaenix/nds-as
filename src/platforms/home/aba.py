"""Pokémon HOME ABA / ABAP decryption (barncastle algorithm, Python port)."""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path

from .rijndael_cipher import rijndael

HOME_ABA_KEY = b"lrZ6++Ln5tLnBsJk.ae6J8BLaLbMhVn@"
HOME_ABA_IV = b"X6TU@VYU$HyqKy57PfwWg7.t7wk2oqtg"
HOME_ABA_BLOCK_SIZE = 32
HOME_ABA_HEADER_BYTES = 1024
UNITY_BUNDLE_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")


@dataclass(slots=True)
class HomeAbaBundle:
    name: str
    data: bytes


@dataclass(slots=True)
class HomeAbapHeader:
    magic: int
    root_head_offset: int
    name_head_offset: int
    data_head_offset: int
    bundles: list[HomeAbaBundle] = field(default_factory=list)

    @property
    def root_buffer_size(self) -> int:
        return self.name_head_offset - self.root_head_offset

    @property
    def data_count(self) -> int:
        return self.root_buffer_size >> 4


def home_aba_available() -> bool:
    return True


def is_likely_unity_bundle(data: bytes) -> bool:
    return any(data.startswith(magic) for magic in UNITY_BUNDLE_MAGICS)


def decrypt_aba_bytes(data: bytes) -> bytes:
    """Decrypt a single .aba payload into a Unity bundle byte stream."""
    if not data:
        raise ValueError("ABA input is empty")
    header_len = min(len(data), HOME_ABA_HEADER_BYTES)
    if header_len <= 0:
        raise ValueError("ABA input is too small to decrypt")
    header = _cbc_decrypt(data[:header_len], HOME_ABA_KEY, HOME_ABA_IV, HOME_ABA_BLOCK_SIZE)
    if len(data) <= header_len:
        return header
    return header + data[header_len:]


def _cbc_decrypt(data: bytes, key: bytes, iv: bytes, block_size: int) -> bytes:
    cipher = rijndael(key, block_size=block_size)
    out = bytearray()
    previous = iv
    for offset in range(0, len(data), block_size):
        block = data[offset : offset + block_size]
        if len(block) < block_size:
            block = block + bytes(block_size - len(block))
        decrypted = cipher.decrypt(block)
        plain = bytes(a ^ b for a, b in zip(decrypted, previous))
        take = min(block_size, len(data) - offset)
        out.extend(plain[:take])
        previous = block
    return bytes(out)


def decrypt_aba_file(path: str | Path, out_path: str | Path | None = None) -> Path:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    decrypted = decrypt_aba_bytes(source.read_bytes())
    target = Path(out_path).expanduser().resolve() if out_path else source.with_suffix(".unity3d")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(decrypted)
    return target


def parse_abap(path: str | Path) -> HomeAbapHeader:
    source = Path(path).expanduser().resolve()
    with source.open("rb") as fh:
        magic, root_head_offset, name_head_offset, data_head_offset = struct.unpack("<4I", fh.read(16))
        header = HomeAbapHeader(
            magic=magic,
            root_head_offset=root_head_offset,
            name_head_offset=name_head_offset,
            data_head_offset=data_head_offset,
        )
        bundles: list[HomeAbaBundle] = []
        for index in range(header.data_count):
            fh.seek(header.root_head_offset + index * 16)
            name_offset, name_size, data_offset, data_size = struct.unpack("<4i", fh.read(16))
            fh.seek(header.name_head_offset + name_offset)
            name = fh.read(name_size).decode("utf-8", "replace").rstrip("\x00")
            fh.seek(header.data_head_offset + data_offset)
            bundles.append(HomeAbaBundle(name=name, data=fh.read(data_size)))
        header.bundles = bundles
        return header


def extract_abap(path: str | Path, out_dir: str | Path) -> list[Path]:
    source = Path(path).expanduser().resolve()
    target_dir = Path(out_dir).expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    header = parse_abap(source)
    written: list[Path] = []
    for bundle in header.bundles:
        out_path = target_dir / f"{bundle.name}.unity3d"
        out_path.write_bytes(decrypt_aba_bytes(bundle.data))
        written.append(out_path)
    return written


def decrypt_aba_to_cache(path: str | Path, cache_dir: str | Path) -> Path:
    """Decrypt an .aba or inner bundle to a stable cache path for UnityPy preview."""
    source = Path(path).expanduser().resolve()
    cache_root = Path(cache_dir).expanduser().resolve()
    cache_root.mkdir(parents=True, exist_ok=True)
    target = cache_root / f"{source.stem}.unity3d"
    if source.suffix.casefold() == ".abap":
        extracted = extract_abap(source, cache_root / f"{source.stem}_abap")
        if not extracted:
            raise RuntimeError(f"ABAP container had no bundles: {source}")
        return extracted[0]
    decrypted = decrypt_aba_bytes(source.read_bytes())
    target.write_bytes(decrypted)
    if not is_likely_unity_bundle(decrypted[:64]):
        # Some HOME payloads are still useful to UnityPy even without a classic magic prefix.
        pass
    return target


def probe_aba_decrypt(path: str | Path) -> dict:
    source = Path(path).expanduser().resolve()
    try:
        decrypted = decrypt_aba_bytes(source.read_bytes())
        return {
            "ok": True,
            "source": str(source),
            "size": source.stat().st_size,
            "decryptedSize": len(decrypted),
            "unityMagic": next((m.decode("ascii") for m in UNITY_BUNDLE_MAGICS if decrypted.startswith(m)), ""),
        }
    except Exception as exc:
        return {"ok": False, "source": str(source), "error": str(exc)}
