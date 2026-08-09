from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

from ...install import project_root

MOBILE_ROM_CACHE_MARKER = ".rae-mobile-rom-source.json"


def is_mobile_rom_archive(path: Path) -> bool:
    return path.is_file() and path.suffix.casefold() == ".rom" and zipfile.is_zipfile(path)


def package_mobile_rom_directory(source_dir: Path, target_rom: Path) -> int:
    """Zip a staged mobile ROM tree into a single .rom archive file."""
    root = Path(source_dir)
    target = Path(target_rom)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()
    count = 0
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            zf.write(path, path.relative_to(root).as_posix())
            count += 1
    return count


def resolve_mobile_rom_root(path: str | Path) -> Path:
    """Return a directory root for scanning a mobile .rom source.

    Legacy directory bundles are returned as-is. Zip archives are extracted into
    a local cache directory keyed by the archive stem.
    """
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    if root.is_dir():
        return root
    if is_mobile_rom_archive(root):
        cache = _mobile_rom_cache_dir(root)
        if _cache_is_fresh(root, cache):
            return cache
        if cache.exists():
            shutil.rmtree(cache)
        cache.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(root) as zf:
            zf.extractall(cache)
        _write_cache_marker(root, cache)
        return cache
    raise ValueError(f"Mobile .rom sources must be zip archives or directory bundles: {root}")


def _mobile_rom_cache_dir(archive: Path) -> Path:
    return project_root() / ".cache" / "mobile-rom" / archive.stem


def _write_cache_marker(archive: Path, cache: Path) -> None:
    stat = archive.stat()
    marker = cache / MOBILE_ROM_CACHE_MARKER
    marker.write_text(
        json.dumps({"source": str(archive), "mtime": stat.st_mtime, "size": stat.st_size}),
        encoding="utf-8",
    )


def _cache_is_fresh(archive: Path, cache: Path) -> bool:
    marker = cache / MOBILE_ROM_CACHE_MARKER
    manifest = cache / "rae_mobile_rom.json"
    if not marker.exists() or not manifest.exists():
        return False
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
        stat = archive.stat()
        return raw.get("mtime") == stat.st_mtime and raw.get("size") == stat.st_size
    except Exception:
        return False
