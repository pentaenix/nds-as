"""On-demand read/export services for Switch descriptor assets."""
from __future__ import annotations

import json
import struct
import tempfile
from pathlib import Path
from typing import Callable

from .container import SwitchRom
from .hash_cache import HashCache
from .keys import SwitchKeys
from .trinity import TrpfsIndex, TrinityArchive

Progress = Callable[[str], None]

_HASH_CACHE_NAME = "sv_hashes_inside_trpak.txt"
_TRPFD_PATH = "/arc/data.trpfd"
_TRPFS_PATH = "/arc/data.trpfs"


def _descriptor(asset) -> dict:
    try:
        return json.loads(asset.data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, AttributeError):
        return {}


def _cache_path(rom_path: str | Path) -> Path:
    return Path(rom_path).expanduser().resolve().parent / _HASH_CACHE_NAME


def open_archive(rom_path: str | Path, progress: Progress | None = None) -> TrinityArchive:
    keys = SwitchKeys.load(rom_path)
    with SwitchRom(rom_path, keys) as rom:
        files = {f.path: f for f in rom.romfs_files()}
        trpfd_entry = files[_TRPFD_PATH]
        trpfs_entry = files[_TRPFS_PATH]
        trpfd = rom.read_file(trpfd_entry)
        head = rom.read_file(trpfs_entry, 0, 16)
        init = struct.unpack_from("<Q", head, 8)[0]
        index_region = rom.read_file(trpfs_entry, init, min(12_000_000, trpfs_entry.size - init))
        trpfs_index = TrpfsIndex.parse_header(head, index_region)

        def read_range(offset: int, size: int) -> bytes:
            with SwitchRom(rom_path, keys) as inner:
                inner_files = {f.path: f for f in inner.romfs_files()}
                return inner.read_file(inner_files[_TRPFS_PATH], offset, size)

        if progress:
            progress("Switch: Trinity archive ready")
        return TrinityArchive(trpfd, trpfs_index, read_trpfs_range=read_range)


def hash_cache_for_rom(rom_path: str | Path, progress: Progress | None = None) -> HashCache:
    return HashCache.ensure(_cache_path(rom_path), progress)


def read_romfs_payload(descriptor: dict, progress: Progress | None = None) -> bytes:
    rom_path = descriptor["rom"]
    romfs_path = descriptor["romfs_path"]
    if progress:
        progress(f"Switch: decrypting {romfs_path}…")
    keys = SwitchKeys.load(rom_path)
    with SwitchRom(rom_path, keys) as rom:
        target = next((f for f in rom.romfs_files() if f.path == romfs_path), None)
        if target is None:
            raise FileNotFoundError(f"{romfs_path} not present in RomFS")
        return rom.read_file(target)


def iter_named_trinity_files(
    archive: TrinityArchive,
    cache: HashCache,
    *,
    suffix: str = "",
    contains: str = "",
):
    """Yield ``(trpak_path, inner_path, file_hash)`` present in this ROM."""
    suf = suffix.lower()
    needle = contains.lower()
    for file_hash, trpak_index in zip(
        archive._trpfd.file_hashes,
        archive._trpfd.file_trpak_index,
        strict=False,
    ):
        if trpak_index >= len(archive._trpfd.trpak_paths):
            continue
        trpak_path = archive._trpfd.trpak_paths[trpak_index]
        if not archive.trpak_in_trpfs(trpak_path):
            continue
        resolved = cache.resolve(file_hash)
        if not resolved:
            continue
        _, inner_path = resolved
        lower = inner_path.lower()
        if suf and not lower.endswith(suf):
            continue
        if needle and needle not in lower:
            continue
        yield trpak_path, inner_path, file_hash


def read_trinity_payload(descriptor: dict, progress: Progress | None = None) -> bytes:
    archive = open_archive(descriptor["rom"], progress)
    trpak_path = archive.resolve_trpak_path(
        descriptor["inner_path"],
        descriptor.get("trpak_path"),
        file_hash=descriptor.get("file_hash"),
    )
    return archive.read_by_path(trpak_path, descriptor["inner_path"])


def build_model_glb(
    descriptor: dict,
    out_path: str | Path,
    progress: Progress | None = None,
) -> Path:
    from .glb import write_model_glb
    from .model import load_model
    from .oodle import OodleError

    if progress:
        progress(f"Switch: building model {descriptor.get('inner_path', '')}…")
    try:
        archive = open_archive(descriptor["rom"], progress)
        trpak_path = archive.resolve_trpak_path(
            descriptor["inner_path"],
            descriptor.get("trpak_path"),
            file_hash=descriptor.get("file_hash"),
        )
        model = load_model(archive, trpak_path, descriptor["inner_path"])
    except OodleError as exc:
        raise RuntimeError(
            f"{exc} Place oo2core (RAE_SWITCH_OODLE_DLL) to preview Switch models."
        ) from exc
    if not model.submeshes:
        raise RuntimeError("No geometry found in model.")
    return write_model_glb(model, out_path)


def export_raw(descriptor: dict, out_dir: str | Path, progress: Progress | None = None) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    kind = descriptor.get("type")
    if kind == "trinity_file":
        payload = read_trinity_payload(descriptor, progress)
        name = Path(descriptor["inner_path"]).name
    elif kind == "romfs_file":
        payload = read_romfs_payload(descriptor, progress)
        name = Path(descriptor.get("romfs_path", "asset")).name or "asset.bin"
    else:
        raise ValueError("Unsupported Switch descriptor type for raw export.")
    path = out_dir / name
    path.write_bytes(payload)
    return [path]


def export_model_glb(descriptor: dict, out_dir: str | Path, progress: Progress | None = None) -> list[Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = Path(descriptor["inner_path"]).stem
    out = out_dir / f"{stem}.glb"
    build_model_glb(descriptor, out, progress)
    return [out]


def preview_model_glb(descriptor: dict, progress: Progress | None = None) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="rae_switch_"))
    return build_model_glb(descriptor, tmp / "preview.glb", progress)
