from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable

UNITY_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")
CANDIDATE_EXTENSIONS = {
    ".aba", ".abap", ".rom", ".unity3d", ".bundle", ".assetbundle", ".assets", ".ress",
    ".resource", ".manifest", ".json", ".bytes", ".bin", ".apk", ".apkm", ".xapk", ".obb",
}
ZIP_EXTENSIONS = {".apk", ".apkm", ".xapk", ".zip", ".obb"}
MAX_INLINE_BYTES = int(os.environ.get("RAE_HOME_MAX_INLINE_BYTES", str(4 * 1024 * 1024)))
MAX_SCAN_FILES = int(os.environ.get("RAE_HOME_MAX_SCAN_FILES", "250000"))


@dataclass(slots=True)
class AndroidSourceFile:
    source_root: str
    virtual_path: str
    local_path: str | None
    container: str | None
    size: int
    extension: str
    magic: str
    classification: str
    partial_sha256: str = ""
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(slots=True)
class AndroidSourceInventory:
    source: str
    package_id: str = ""
    files: list[AndroidSourceFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "packageId": self.package_id,
            "files": [f.to_dict() for f in self.files],
            "warnings": list(self.warnings),
        }

    def write_json(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        return out


def scan_android_source(path: str | Path, progress: Callable[[str], None] | None = None) -> AndroidSourceInventory:
    source = Path(path).expanduser().resolve()
    inv = AndroidSourceInventory(source=str(source))
    if not source.exists():
        raise FileNotFoundError(source)
    if source.is_file():
        _scan_file(source, inv, progress=progress, root=source.parent)
    else:
        count = 0
        for p in source.rglob("*"):
            if not p.is_file():
                continue
            count += 1
            if count > MAX_SCAN_FILES:
                inv.warnings.append(f"scan stopped after {MAX_SCAN_FILES:,} files; set RAE_HOME_MAX_SCAN_FILES to raise the cap")
                break
            _scan_file(p, inv, progress=progress, root=source)
    _infer_package(inv)
    return inv


def _scan_file(path: Path, inv: AndroidSourceInventory, *, progress: Callable[[str], None] | None, root: Path) -> None:
    suffix = path.suffix.casefold()
    try:
        size = path.stat().st_size
    except OSError as exc:
        inv.warnings.append(f"could not stat {path}: {exc}")
        return
    if suffix in ZIP_EXTENSIONS and zipfile.is_zipfile(path):
        _scan_zip(path, inv, progress=progress)
        return
    try:
        header = path.read_bytes()[:128]
    except OSError as exc:
        inv.warnings.append(f"could not read {path}: {exc}")
        return
    rel = str(path.relative_to(root)).replace("\\", "/") if path != root else path.name
    entry = AndroidSourceFile(
        source_root=str(root),
        virtual_path=rel,
        local_path=str(path),
        container=None,
        size=size,
        extension=suffix,
        magic=_magic_name(header),
        classification=classify_candidate(rel, suffix, header),
        partial_sha256=_partial_sha256(path),
    )
    if entry.classification != "ignored":
        inv.files.append(entry)
        if progress:
            progress(f"HOME candidate: {entry.classification} {entry.virtual_path}")


def _scan_zip(path: Path, inv: AndroidSourceInventory, *, progress: Callable[[str], None] | None) -> None:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            for name in names:
                if name.endswith("/"):
                    continue
                ext = PurePosixPath(name).suffix.casefold()
                info = zf.getinfo(name)
                header = b""
                if ext in CANDIDATE_EXTENSIONS or _homeish_path(name):
                    try:
                        with zf.open(name) as fh:
                            header = fh.read(128)
                    except Exception:
                        header = b""
                classification = classify_candidate(name, ext, header)
                if classification == "ignored" and not _homeish_path(name):
                    continue
                entry = AndroidSourceFile(
                    source_root=str(path),
                    virtual_path=f"{path.name}!/{name}",
                    local_path=None,
                    container=str(path),
                    size=int(info.file_size),
                    extension=ext,
                    magic=_magic_name(header),
                    classification=classification if classification != "ignored" else "android-package-member",
                )
                inv.files.append(entry)
                if progress:
                    progress(f"HOME candidate in {path.name}: {entry.classification} {name}")
    except Exception as exc:
        inv.warnings.append(f"could not inspect zip {path}: {exc}")


def classify_candidate(virtual_path: str, suffix: str, header: bytes) -> str:
    low = virtual_path.casefold()
    if header.startswith(UNITY_MAGICS):
        return "unity-readable-bundle"
    if suffix in {".aba", ".abap"}:
        return "home-aba-package"
    if suffix in {".unity3d", ".bundle", ".assetbundle", ".assets", ".resource", ".ress"}:
        return "unity-candidate"
    if suffix in {".manifest", ".json", ".bytes", ".bin"} and (_homeish_path(low) or "assetbundle" in low or "catalog" in low):
        return "home-metadata-candidate"
    if _homeish_path(low):
        return "home-path-candidate"
    return "ignored"


def _homeish_path(path: str) -> bool:
    low = path.casefold()
    return any(token in low for token in (
        "jp.pokemon.pokemonhome", "models/android", "/pokemons/", "pokemons/pm", "mitake", "/dependencies/", "dependencies/",
    ))


def _magic_name(header: bytes) -> str:
    for magic in UNITY_MAGICS:
        if header.startswith(magic):
            return magic.decode("ascii")
    if header.startswith(b"PK\x03\x04"):
        return "ZIP"
    if header.startswith(b"CAB-"):
        return "CAB"
    if header:
        try:
            text = header[:8].decode("ascii")
            if text.isprintable():
                return text.strip("\x00")
        except Exception:
            pass
    return ""


def _partial_sha256(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            remaining = MAX_INLINE_BYTES
            while remaining > 0:
                chunk = fh.read(min(65536, remaining))
                if not chunk:
                    break
                h.update(chunk)
                remaining -= len(chunk)
    except OSError:
        return ""
    return h.hexdigest()



def _read_mobile_rom_manifest(source: Path) -> dict:
    if not source.is_dir():
        return {}
    manifest = source / "rae_mobile_rom.json"
    if not manifest.exists():
        return {}
    try:
        return json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _infer_package(inv: AndroidSourceInventory) -> None:
    manifest = _read_mobile_rom_manifest(Path(inv.source))
    package_id = str(manifest.get("packageId") or manifest.get("package_id") or "").strip()
    if package_id:
        inv.package_id = package_id
        return
    haystack = "\n".join(f.virtual_path for f in inv.files[:10000]).casefold()
    if "jp.pokemon.pokemonhome" in haystack or "pokemonhome" in haystack:
        inv.package_id = "jp.pokemon.pokemonhome"
