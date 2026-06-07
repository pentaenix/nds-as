from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .compression import decompress_lz10, looks_like_lz10
from .narc import NarcArchive, looks_like_narc
from .nds import NDSRom
from .util import read_u32le
from .nitro_2d import NITRO_2D_MAGICS
from .audio import AUDIO_MAGICS, iter_sdat_files, iter_swar_swavs

NITRO_MAGICS: dict[bytes, tuple[str, str]] = {
    b"BMD0": ("Model", ".nsbmd"),
    b"BTX0": ("Texture archive", ".nsbtx"),
    b"BCA0": ("Skeletal animation", ".nsbca"),
    b"BTA0": ("Texture SRT animation", ".nsbta"),
    b"BTP0": ("Texture pattern animation", ".nsbtp"),
    b"BMA0": ("Material animation", ".nsbma"),
    b"BVA0": ("Visibility animation", ".nsbva"),
    b"BPC0": ("Color animation", ".nsbpc"),
    **NITRO_2D_MAGICS,
    b"\x89PNG": ("PNG image", ".png"),
    **AUDIO_MAGICS,
}
KNOWN_EXTENSIONS = {
    ".nsbmd", ".nsbtx", ".nsbca", ".nsbta", ".nsbtp", ".nsbma", ".nsbva", ".nsbpc",
    ".ncgr", ".nclr", ".nscr", ".ncer", ".nanr", ".nftr", ".png",
    ".sdat", ".sseq", ".ssar", ".sbnk", ".swar", ".swav", ".strm",
}

# Hard safety caps. These keep DSM from turning a malformed/odd ROM blob into
# a runaway allocation. Audio archives are expanded during explicit export or
# explicit export actions, not during initial ROM load.
MAX_LZ10_DECOMPRESSED_SIZE = 16 * 1024 * 1024
MAX_SCAN_ASSETS = 50000
MAX_CONTAINER_CHILDREN = 25000


@dataclass(slots=True)
class Asset:
    asset_id: str
    virtual_path: str
    kind: str
    magic: str
    extension: str
    data: bytes
    original_data: bytes
    rom_file_id: int | None = None
    rom_offset: int | None = None
    compressed: bool = False
    container_chain: tuple[str, ...] = field(default_factory=tuple)
    carved: bool = False
    carved_offset: int | None = None
    mapping_category: str = "unknown"
    mapping_label: str = ""
    mapping_confidence: str = ""

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def original_size(self) -> int:
        return len(self.original_data)

    @property
    def folder_key(self) -> str:
        path = PurePosixPath(self.virtual_path)
        if len(path.parts) <= 1:
            return ""
        return str(path.parent)

    @property
    def suggested_filename(self) -> str:
        base = PurePosixPath(self.virtual_path).name or self.asset_id
        lower = base.lower()
        if not lower.endswith(self.extension):
            base = f"{base}{self.extension}"
        return base


def identify_nitro(data: bytes) -> tuple[str, str, str] | None:
    if len(data) < 4:
        return None
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        kind, ext = NITRO_MAGICS[b"\x89PNG"]
        return kind, "PNG", ext
    magic = data[:4]
    if magic in NITRO_MAGICS:
        kind, ext = NITRO_MAGICS[magic]
        return kind, magic.decode("ascii", errors="replace"), ext
    return None


def scan_nds_path(
    path: str,
    progress: Callable[[str], None] | None = None,
    *,
    carve_unknown_blobs: bool = True,
    expand_audio_archives: bool = False,
) -> list[Asset]:
    rom = NDSRom.from_path(path)
    assets: list[Asset] = []
    files = list(rom.iter_files())
    total = len(files)

    for index, rom_file in enumerate(files, start=1):
        if progress and (index == 1 or index % 25 == 0 or index == total):
            progress(f"Scanning ROM file {index}/{total}: {rom_file.path}")
        assets.extend(
            scan_blob(
                rom_file.path,
                rom_file.data,
                original_data=rom_file.data,
                rom_file_id=rom_file.file_id,
                rom_offset=rom_file.start,
                container_chain=(),
                depth=0,
                carve_unknown_blobs=carve_unknown_blobs,
                expand_audio_archives=expand_audio_archives,
            )
        )
        if len(assets) > MAX_SCAN_ASSETS:
            if progress:
                progress(f"Safety stop: scan reached {len(assets)} detected assets. Refine with mappings/deep scan later if needed.")
            break
    deduped = _dedupe_assets(assets)
    try:
        from .mapping import choose_mapping, apply_mapping_to_assets
        mapping = choose_mapping(rom.info.title, rom.info.game_code)
        apply_mapping_to_assets(deduped, mapping)
    except Exception:
        pass
    return deduped


def scan_blob(
    virtual_path: str,
    data: bytes,
    *,
    original_data: bytes | None = None,
    rom_file_id: int | None = None,
    rom_offset: int | None = None,
    compressed: bool = False,
    container_chain: tuple[str, ...] = (),
    depth: int = 0,
    max_depth: int = 10,
    carve_unknown_blobs: bool = True,
    expand_audio_archives: bool = False,
) -> list[Asset]:
    if depth > max_depth:
        return []

    original = data if original_data is None else original_data
    assets: list[Asset] = []

    if looks_like_lz10(data):
        try:
            expected_size = data[1] | (data[2] << 8) | (data[3] << 16)
            if expected_size > MAX_LZ10_DECOMPRESSED_SIZE:
                raise ValueError(f"Skipping suspicious LZ10 stream with {expected_size} byte output cap")
            decoded = decompress_lz10(data)
            assets.extend(
                scan_blob(
                    virtual_path + "#lz10",
                    decoded,
                    original_data=data,
                    rom_file_id=rom_file_id,
                    rom_offset=rom_offset,
                    compressed=True,
                    container_chain=container_chain,
                    depth=depth + 1,
                    max_depth=max_depth,
                    carve_unknown_blobs=carve_unknown_blobs,
                    expand_audio_archives=expand_audio_archives,
                )
            )
            # Do not return yet; some false-positive LZ files are still worth checking raw.
        except Exception:
            pass

    info = identify_nitro(data)
    if info:
        kind, magic, ext = info
        asset_id = _asset_id(virtual_path, data)
        assets.append(
            Asset(
                asset_id=asset_id,
                virtual_path=_with_ext_hint(virtual_path, ext),
                kind=kind,
                magic=magic,
                extension=ext,
                data=data,
                original_data=original,
                rom_file_id=rom_file_id,
                rom_offset=rom_offset,
                compressed=compressed,
                container_chain=container_chain,
            )
        )

    if looks_like_narc(data):
        try:
            archive = NarcArchive(data, virtual_path)
            for child_index, entry in enumerate(archive.iter_files()):
                if child_index >= MAX_CONTAINER_CHILDREN:
                    break
                child_path = f"{virtual_path}/{entry.path}"
                assets.extend(
                    scan_blob(
                        child_path,
                        entry.data,
                        original_data=entry.data,
                        rom_file_id=rom_file_id,
                        rom_offset=rom_offset,
                        compressed=False,
                        container_chain=container_chain + (virtual_path,),
                        depth=depth + 1,
                        max_depth=max_depth,
                        carve_unknown_blobs=carve_unknown_blobs,
                        expand_audio_archives=expand_audio_archives,
                    )
                )
        except Exception:
            pass

    # SDAT is the normal Nintendo DS sound archive container. Extracting its
    # children lets DSM list/export music sequences, banks, wave archives,
    # individual samples, and streamed tracks without modifying the ROM.
    if expand_audio_archives and data.startswith(b"SDAT"):
        try:
            for entry in iter_sdat_files(data):
                child_path = f"{virtual_path}/{entry.path}"
                assets.extend(
                    scan_blob(
                        child_path,
                        entry.data,
                        original_data=entry.data,
                        rom_file_id=rom_file_id,
                        rom_offset=(rom_offset + entry.offset) if rom_offset is not None else None,
                        compressed=False,
                        container_chain=container_chain + (virtual_path,),
                        depth=depth + 1,
                        max_depth=max_depth,
                        carve_unknown_blobs=False,
                        expand_audio_archives=False,
                    )
                )
        except Exception:
            pass

    # SWAR is a folder of unnamed SWAV sample clips. Pull them out so SFX and
    # instrument samples can be decoded/exported as WAV when possible.
    if expand_audio_archives and data.startswith(b"SWAR"):
        try:
            for entry in iter_swar_swavs(data):
                child_path = f"{virtual_path}/{entry.path}"
                assets.extend(
                    scan_blob(
                        child_path,
                        entry.data,
                        original_data=entry.data,
                        rom_file_id=rom_file_id,
                        rom_offset=(rom_offset + entry.offset) if rom_offset is not None else None,
                        compressed=False,
                        container_chain=container_chain + (virtual_path,),
                        depth=depth + 1,
                        max_depth=max_depth,
                        carve_unknown_blobs=False,
                        expand_audio_archives=False,
                    )
                )
        except Exception:
            pass

    # Extra fallback for "any DS game": some tools/games wrap Nitro files in
    # custom containers. Carving catches embedded BMD0/BTX0/etc. with valid Nitro sizes.
    if carve_unknown_blobs and data and not info:
        for carved_path, carved_data, offset in carve_nitro_files(virtual_path, data):
            carved_info = identify_nitro(carved_data)
            if not carved_info:
                continue
            kind, magic, ext = carved_info
            assets.append(
                Asset(
                    asset_id=_asset_id(carved_path, carved_data),
                    virtual_path=_with_ext_hint(carved_path, ext),
                    kind=kind,
                    magic=magic,
                    extension=ext,
                    data=carved_data,
                    original_data=carved_data,
                    rom_file_id=rom_file_id,
                    rom_offset=(rom_offset + offset) if rom_offset is not None else None,
                    compressed=compressed,
                    container_chain=container_chain + ((virtual_path + "#carved"),),
                    carved=True,
                    carved_offset=offset,
                )
            )

    return assets


def carve_nitro_files(virtual_path: str, data: bytes) -> list[tuple[str, bytes, int]]:
    found: list[tuple[str, bytes, int]] = []
    if len(data) < 16:
        return found

    for magic in NITRO_MAGICS:
        start = 0
        while True:
            pos = data.find(magic, start)
            if pos < 0:
                break
            start = pos + 4
            if pos == 0:
                continue
            if not _looks_like_nitro_header_at(data, pos):
                continue
            size = read_u32le(data, pos + 8)
            if size <= 0 or pos + size > len(data):
                continue
            blob = data[pos:pos + size]
            found.append((f"{virtual_path}#carved_0x{pos:X}", blob, pos))
    found.sort(key=lambda item: item[2])
    return found


def _looks_like_nitro_header_at(data: bytes, pos: int) -> bool:
    if pos + 16 > len(data):
        return False
    bom = data[pos + 4:pos + 6]
    if bom not in {b"\xFE\xFF", b"\xFF\xFE"}:
        return False
    file_size = read_u32le(data, pos + 8)
    header_size = int.from_bytes(data[pos + 12:pos + 14], "little", signed=False)
    block_count = int.from_bytes(data[pos + 14:pos + 16], "little", signed=False)
    remaining = len(data) - pos
    return 16 <= file_size <= remaining and 8 <= header_size <= 0x200 and 1 <= block_count <= 64


def asset_search_text(asset: Asset) -> str:
    """Precomputed lowercase metadata used by browser text filters."""
    return " ".join([
        asset.virtual_path,
        asset.kind,
        asset.magic,
        getattr(asset, "mapping_category", ""),
        getattr(asset, "mapping_label", ""),
        getattr(asset, "mapping_confidence", ""),
    ]).casefold()


def filter_assets(assets: Iterable[Asset], query: str | None) -> list[Asset]:
    """Filter assets with friendly text and small structured helpers.

    Plain terms are ANDed: ``battle grass`` means both words should appear in
    the asset's searchable metadata. Alternatives can be separated with ``|``.

    Supported prefixes:
      magic:BMD0, cat:move-effects, category:textures, path:a/0/2/2, label:map
      mapped, unmapped
    """
    if not query:
        return list(assets)
    alternatives = [part.strip() for part in query.split("|") if part.strip()] or [query.strip()]
    return filter_assets_indexed(assets, query, None)


def filter_assets_indexed(
    assets: Iterable[Asset],
    query: str | None,
    search_text_by_id: dict[str, str] | None,
) -> list[Asset]:
    """Filter assets using optional precomputed search text keyed by asset_id."""
    if not query:
        return list(assets)
    alternatives = [part.strip() for part in query.split("|") if part.strip()] or [query.strip()]
    return [
        asset for asset in assets
        if any(_asset_matches_query(asset, alt, search_text_by_id) for alt in alternatives)
    ]


def _asset_matches_query(asset: Asset, query: str, search_text_by_id: dict[str, str] | None = None) -> bool:
    terms = [t for t in query.casefold().split() if t]
    if not terms:
        return True
    searchable = (search_text_by_id or {}).get(asset.asset_id) or asset_search_text(asset)
    for term in terms:
        if term == "mapped":
            if not getattr(asset, "mapping_label", "") or getattr(asset, "mapping_confidence", "") == "format-signature":
                return False
            continue
        if term == "unmapped":
            if getattr(asset, "mapping_label", "") and getattr(asset, "mapping_confidence", "") != "format-signature":
                return False
            continue
        if term.startswith(("cat:", "category:")):
            value = term.split(":", 1)[1]
            if value not in getattr(asset, "mapping_category", "").casefold():
                return False
            continue
        if term.startswith("magic:"):
            value = term.split(":", 1)[1]
            if value != asset.magic.casefold():
                return False
            continue
        if term.startswith("path:"):
            value = term.split(":", 1)[1]
            if value not in asset.virtual_path.casefold():
                return False
            continue
        if term.startswith("label:"):
            value = term.split(":", 1)[1]
            if value not in getattr(asset, "mapping_label", "").casefold():
                return False
            continue
        if term not in searchable:
            return False
    return True


def _dedupe_assets(assets: Iterable[Asset]) -> list[Asset]:
    seen: set[tuple[str, str, str]] = set()
    deduped: list[Asset] = []
    for asset in assets:
        key = (asset.virtual_path, asset.magic, asset.asset_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(asset)
    return deduped


ASSET_TYPE_GROUPS: dict[str, tuple[str, ...]] = {
    "models": ("BMD0",),
    "textures": ("BTX0",),
    "sprites": ("RGCN", "RLCN", "RCSN", "RECN", "RNAN", "NFTR", "PNG"),
    "audio": ("SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"),
    "animations": ("BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"),
}


def filter_assets_by_types(assets: Iterable[Asset], enabled_types: Iterable[str]) -> list[Asset]:
    """Keep assets whose magic matches any enabled type key.

    Keys may be legacy group names (``models``, ``textures``, …) or individual
    Nitro magic codes (``RLCN``, ``BMD0``, …).
    """
    kinds = [str(kind).strip() for kind in enabled_types if str(kind).strip()]
    if not kinds:
        return list(assets)
    allowed: set[str] = set()
    for kind in kinds:
        grouped = ASSET_TYPE_GROUPS.get(kind)
        if grouped:
            allowed.update(grouped)
        else:
            allowed.add(kind)
    if not allowed:
        return list(assets)
    return [asset for asset in assets if asset.magic in allowed]


def _asset_id(path: str, data: bytes) -> str:
    digest = hashlib.sha1(path.encode("utf-8") + b"\0" + data[:4096]).hexdigest()[:16]
    return digest


def _with_ext_hint(path: str, ext: str) -> str:
    lower = path.lower()
    if any(lower.endswith(e) for e in KNOWN_EXTENSIONS):
        return path
    # Preserve the original numbered filename but add a real useful extension.
    return f"{path}{ext}"
