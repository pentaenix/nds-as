from __future__ import annotations

import json
import re
from pathlib import Path

from ...install import project_root
from .assetstudio_preview import assetstudio_available, home_cache_directories, resolve_assetstudio_cli
from .ids import parse_home_asset_id

_PM_TOKEN_RE = re.compile(r"pm(\d{4})", re.IGNORECASE)


def cache_index_path(mobile_root: Path) -> Path:
    return project_root() / ".cache" / "home-species-index" / f"{mobile_root.name}.json"


_FINGERPRINT_MEMO: dict[str, tuple[float, str]] = {}
_FINGERPRINT_TTL_SECONDS = 30.0


def _cache_fingerprint(mobile_root: Path) -> str:
    """Cheap fingerprint of the HOME Cache folders (file count + max mtime + size).

    Memoized for a short window because asset labeling calls this once per row.
    """
    import time

    key = str(mobile_root)
    now = time.monotonic()
    memo = _FINGERPRINT_MEMO.get(key)
    if memo is not None and now - memo[0] < _FINGERPRINT_TTL_SECONDS:
        return memo[1]
    value = _compute_cache_fingerprint(mobile_root)
    _FINGERPRINT_MEMO[key] = (now, value)
    return value


def _compute_cache_fingerprint(mobile_root: Path) -> str:
    count = 0
    latest = 0.0
    total = 0
    for cache_dir in home_cache_directories(mobile_root):
        for path in cache_dir.rglob("*"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError:
                continue
            count += 1
            total += stat.st_size
            latest = max(latest, stat.st_mtime)
    return f"{count}:{total}:{int(latest)}"


def load_cache_species_index(mobile_root: Path) -> set[int]:
    path = cache_index_path(mobile_root)
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            stored_fp = str(raw.get("cacheFingerprint") or "")
            if stored_fp and stored_fp != _cache_fingerprint(mobile_root):
                return set()  # Cache changed (re-extracted ROM) — force a rebuild.
            return {int(x) for x in raw.get("previewableSpecies", [])}
        except Exception:
            pass
    return set()


def save_cache_species_index(mobile_root: Path, species: set[int]) -> Path:
    path = cache_index_path(mobile_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "mobileRoot": str(mobile_root),
                "previewableSpecies": sorted(species),
                "cacheFingerprint": _cache_fingerprint(mobile_root),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def build_cache_species_index(mobile_root: Path, *, progress=None) -> set[int]:
    if not assetstudio_available():
        return set()
    cache_dirs = home_cache_directories(mobile_root)
    if not cache_dirs:
        return set()
    found: set[int] = set()
    for cache_dir in cache_dirs:
        if progress:
            progress(f"Indexing previewable HOME Cache species in {cache_dir.name}…")
        for token in _discover_pm_tokens_in_cache(cache_dir, progress=progress):
            try:
                found.add(int(token))
            except ValueError:
                continue
    if found:
        save_cache_species_index(mobile_root, found)
        if progress:
            progress(f"HOME Cache index: {len(found):,} species with previewable meshes.")
    return found


def ensure_cache_species_index(mobile_root: Path, *, progress=None, rebuild: bool = False) -> set[int]:
    mobile_root = mobile_root.expanduser().resolve()
    if not rebuild:
        existing = load_cache_species_index(mobile_root)
        if existing:
            return existing
    return build_cache_species_index(mobile_root, progress=progress)


def species_preview_status(mobile_root: Path | None, species_number: int) -> str:
    if mobile_root is None:
        return "unknown"
    previewable = load_cache_species_index(mobile_root)
    if species_number in previewable:
        return "cache-ready"
    if _tyranitar_stub_exists(mobile_root, species_number):
        return "encrypted-stub"
    return "missing"


def preview_status_label(status: str) -> str:
    return {
        "cache-ready": "previewable in HOME Cache",
        "encrypted-stub": "encrypted tyranitar stub (block-encrypted)",
        "missing": "not downloaded on device",
        "unknown": "preview status unknown",
    }.get(status, status)


def _tyranitar_stub_exists(mobile_root: Path, species_number: int) -> bool:
    token = f"{species_number:04d}"
    for folder in (
        mobile_root / "external_files/files/tyranitar",
        mobile_root / "candidates/files/tyranitar",
    ):
        if not folder.is_dir():
            continue
        if any(folder.glob(f"cap{token}*.aba")):
            return True
        if any(folder.glob(f"mt_pv_ev_{token}*.aba")):
            return True
    return False


def _discover_pm_tokens_in_cache(cache_dir: Path, *, progress=None) -> set[str]:
    """Discover pm#### tokens by exporting AssetStudio's asset list (one pass per cache folder).

    ``-m info`` alone only prints per-type counts, so the pm#### tokens have to
    come from the XML asset list where every Mesh name/container is spelled out.
    """
    import os
    import subprocess
    import tempfile
    import xml.etree.ElementTree as ET

    cli = resolve_assetstudio_cli()
    if cli is None:
        return set()
    env = os.environ.copy()
    env.setdefault("DOTNET_ROLL_FORWARD", "LatestMajor")

    tokens: set[str] = set()
    with tempfile.TemporaryDirectory(prefix="rae-home-index-") as tmp:
        args = [
            str(cache_dir),
            "-m", "info",
            "-t", "Mesh",
            "--load-all",
            "--export-asset-list", "xml",
            "-o", tmp,
        ]
        if cli.suffix.casefold() == ".dll":
            command = ["dotnet", str(cli), *args]
            cwd = cli.parent
        else:
            command = [str(cli), *args]
            cwd = cli.parent if cli.parent.exists() else None
        subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)

        for xml_path in Path(tmp).glob("*.xml"):
            try:
                tree = ET.parse(xml_path)
            except ET.ParseError:
                continue
            for asset in tree.getroot():
                name = asset.findtext("Name") or ""
                container = asset.findtext("Container") or ""
                for match in _PM_TOKEN_RE.finditer(f"{name} {container}"):
                    tokens.add(match.group(1))
    return tokens
