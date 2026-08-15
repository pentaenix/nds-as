"""ISO and InstallShield catalog access for the Windows CD/ISO platform.

Scanning deliberately stops at catalog metadata.  The large cabinet payloads are
cached on disk so later preview/export code can extract individual files lazily;
they are never copied into :class:`~rae.core.assets.Asset` rows.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


class WindowsIsoContainerError(RuntimeError):
    """A required container tool failed or returned an unusable catalog."""


@dataclass(frozen=True, slots=True)
class IsoEntry:
    path: str
    size: int
    is_dir: bool = False


@dataclass(frozen=True, slots=True)
class InstallShieldEntry:
    path: str
    size: int

    @property
    def extension(self) -> str:
        return Path(self.path.replace("\\", "/")).suffix.casefold()


def find_7z() -> str | None:
    """Return a usable 7-Zip executable from PATH."""
    return shutil.which("7z") or shutil.which("7zz")


def find_unshield() -> str | None:
    """Find unshield, honoring an explicit user installation first."""
    configured = os.environ.get("RAE_UNSHIELD", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        return None
    return shutil.which("unshield")


def _run(command: Sequence[str]) -> str:
    try:
        result = subprocess.run(
            list(command),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as exc:
        raise WindowsIsoContainerError(f"Could not run {command[0]}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1] if detail else f"exit status {result.returncode}"
        raise WindowsIsoContainerError(f"{Path(command[0]).name} failed: {message}")
    return result.stdout


def parse_7z_slt(text: str) -> list[IsoEntry]:
    """Parse the stable ``7z l -slt`` key/value listing format."""
    marker = "----------"
    if marker not in text:
        return []
    records: list[IsoEntry] = []
    for block in text.split(marker, 1)[1].strip().split("\n\n"):
        values: dict[str, str] = {}
        for line in block.splitlines():
            if " = " not in line:
                continue
            key, value = line.split(" = ", 1)
            values[key.strip()] = value.strip()
        path = values.get("Path", "")
        if not path:
            continue
        is_dir = values.get("Folder") == "+"
        try:
            size = int(values.get("Size") or 0)
        except ValueError:
            size = 0
        records.append(IsoEntry(path=_portable_path(path), size=size, is_dir=is_dir))
    return records


def list_iso_entries(source: Path, seven_zip: str) -> list[IsoEntry]:
    entries = parse_7z_slt(_run((seven_zip, "l", "-slt", str(source))))
    if not entries:
        raise WindowsIsoContainerError(f"7-Zip found no files in {source.name}")
    return entries


def default_cache_root() -> Path:
    # container.py -> windows_iso -> platforms -> src -> RAE repository root
    return Path(__file__).resolve().parents[3] / ".cache" / "windows_iso"


def cache_directory(source: Path, cache_root: Path | None = None) -> Path:
    stat = source.stat()
    identity = f"{source.resolve()}\0{stat.st_size}\0{stat.st_mtime_ns}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    safe_stem = re.sub(r"[^a-z0-9._-]+", "_", source.stem.casefold()).strip("._")
    return (cache_root or default_cache_root()) / f"{safe_stem or 'disc'}-{digest}"


def cache_installshield_cabinets(
    source: Path,
    iso_entries: Iterable[IsoEntry],
    seven_zip: str,
    *,
    member_names: Sequence[str] = ("data1.hdr", "data1.cab", "data2.cab"),
    cache_root: Path | None = None,
) -> Path:
    """Extract the installer cabinet set once and return its cache directory."""
    available = {entry.path.casefold(): entry for entry in iso_entries if not entry.is_dir}
    selected: list[IsoEntry] = []
    for name in member_names:
        entry = available.get(_portable_path(name).casefold())
        if entry is None:
            raise WindowsIsoContainerError(f"ISO is missing required installer member {name}")
        selected.append(entry)

    destination = cache_directory(source, cache_root)
    destination.mkdir(parents=True, exist_ok=True)
    if _cached_members_complete(destination, selected):
        return destination

    command = [seven_zip, "e", "-y", f"-o{destination}", str(source)]
    command.extend(entry.path for entry in selected)
    _run(command)
    if not _cached_members_complete(destination, selected):
        raise WindowsIsoContainerError("7-Zip did not produce a complete InstallShield cabinet set")
    return destination


def _cached_members_complete(destination: Path, entries: Iterable[IsoEntry]) -> bool:
    for entry in entries:
        candidate = destination / Path(entry.path).name
        try:
            if not candidate.is_file() or candidate.stat().st_size != entry.size:
                return False
        except OSError:
            return False
    return True


_UNSHIELD_ENTRY = re.compile(r"^\s*(\d+)\s{2,}(.+?)\s*$")


def parse_unshield_listing(text: str) -> list[InstallShieldEntry]:
    entries: list[InstallShieldEntry] = []
    for line in text.splitlines():
        match = _UNSHIELD_ENTRY.match(line)
        if not match:
            continue
        path = match.group(2)
        if path.startswith("<"):
            # InstallShield engine/support files are not game assets.
            continue
        entries.append(InstallShieldEntry(path=_portable_path(path), size=int(match.group(1))))
    return entries


def list_installshield_entries(cache_dir: Path, unshield: str) -> list[InstallShieldEntry]:
    """List cabinet members, reusing a metadata-only JSON catalog when present."""
    catalog_path = cache_dir / "installshield-catalog.json"
    cabinet = cache_dir / "data1.cab"
    header = cache_dir / "data1.hdr"
    if not cabinet.is_file() or not header.is_file():
        raise WindowsIsoContainerError("Cached InstallShield header/cabinet set is incomplete")

    if catalog_path.is_file():
        try:
            raw = json.loads(catalog_path.read_text(encoding="utf-8"))
            cached = [InstallShieldEntry(path=str(row["path"]), size=int(row["size"])) for row in raw]
            if cached:
                return cached
        except (OSError, ValueError, KeyError, TypeError):
            pass

    entries = parse_unshield_listing(_run((unshield, "l", str(cabinet))))
    if not entries:
        raise WindowsIsoContainerError("unshield returned an empty installer catalog")
    try:
        catalog_path.write_text(
            json.dumps([asdict(entry) for entry in entries], ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        # The catalog cache is an optimization; a read-only cache may still scan.
        pass
    return entries


def extract_installshield_member(
    cache_dir: Path,
    member_path: str,
    unshield: str | None = None,
) -> Path:
    """Extract one catalog member lazily and return its stable cache path."""
    portable = _portable_path(member_path)
    destination = Path(cache_dir) / "extracted"
    expected = destination.joinpath(*PurePosixPath(portable).parts)
    if expected.is_file():
        return expected
    executable = unshield or find_unshield()
    if not executable:
        raise WindowsIsoContainerError(
            "unshield was not found; install it or set RAE_UNSHIELD"
        )
    cabinet = Path(cache_dir) / "data1.cab"
    if not cabinet.is_file():
        raise WindowsIsoContainerError("cached data1.cab is unavailable")
    destination.mkdir(parents=True, exist_ok=True)
    # unshield matches basenames reliably even when the catalog uses Windows
    # backslashes. It may extract case-only duplicates; select the exact path.
    _run((executable, "-d", str(destination), "x", str(cabinet), PurePosixPath(portable).name))
    if expected.is_file():
        return expected
    matches = [
        path
        for path in destination.rglob(PurePosixPath(portable).name)
        if path.is_file() and path.as_posix().casefold().endswith(portable.casefold())
    ]
    if len(matches) == 1:
        return matches[0]
    raise WindowsIsoContainerError(f"unshield did not extract {portable}")


def _portable_path(value: str) -> str:
    return value.replace("\\", "/").lstrip("/")
