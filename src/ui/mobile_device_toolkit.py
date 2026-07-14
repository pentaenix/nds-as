
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QInputDialog, QMenu, QMessageBox

from ..install import project_root

POKEMON_HOME_PACKAGE = "jp.pokemon.pokemonhome"
KNOWN_APP_ROM_NAMES = {
    POKEMON_HOME_PACKAGE: "pokemon_home",
}
CANDIDATE_RE = re.compile(
    r"(unity|bundle|assetbundle|\.unity3d|\.aba$|\.abap$|Models|pokemons|pm[0-9]{4}|mitake|cache|catalog|addressable|dependencies)",
    re.IGNORECASE,
)


def install_mobile_device_toolkit(window) -> None:
    """Install generic device/mobile extraction actions on the main window."""
    menubar = window.menuBar()
    menu = _menu_named(menubar, "Device Toolkit") or menubar.addMenu("Device Toolkit")
    mobile_menu = _submenu_named(menu, "Mobile") or menu.addMenu("Mobile")

    # Avoid duplicate actions when the UI is reloaded during development.
    for action in list(mobile_menu.actions()):
        if action.text() in {"Fetch Pokémon HOME to roms/pokemon_home…", "Extract Installed App ROM…"}:
            mobile_menu.removeAction(action)

    extract_action = QAction("Extract Installed App ROM…", window)
    extract_action.triggered.connect(lambda: _extract_installed_app_rom(window))
    mobile_menu.addAction(extract_action)


def _menu_named(menubar, title: str):
    for action in menubar.actions():
        menu = action.menu()
        if menu is not None and action.text().replace("&", "") == title:
            return menu
    return None


def _submenu_named(menu, title: str):
    for action in menu.actions():
        sub = action.menu()
        if sub is not None and action.text().replace("&", "") == title:
            return sub
    return None


def _extract_installed_app_rom(window) -> None:
    adb = shutil.which("adb")
    if not adb:
        QMessageBox.warning(
            window,
            "adb not found",
            "Install Android Platform Tools first. On macOS with Homebrew:\n\n  brew install android-platform-tools",
        )
        return
    try:
        serial = _connected_android_serial(adb, window._update_status)
        packages = _installed_packages(adb, serial)
    except Exception as exc:
        QMessageBox.critical(window, "Android device not ready", str(exc))
        return
    if not packages:
        QMessageBox.information(window, "No apps found", "No non-system Android packages were returned by adb.")
        return
    packages = sorted(packages)
    default_index = packages.index(POKEMON_HOME_PACKAGE) if POKEMON_HOME_PACKAGE in packages else 0
    package_id, ok = QInputDialog.getItem(
        window,
        "Extract Installed App ROM",
        "Choose an installed Android package to extract into roms/<app>.rom:",
        packages,
        default_index,
        False,
    )
    if not ok or not package_id:
        return
    app_name = _safe_app_name(package_id)
    target = project_root() / "roms" / f"{app_name}.rom"
    if target.exists():
        confirm = QMessageBox.question(
            window,
            "Refetch mobile ROM?",
            f"{target} already exists.\n\nDelete it and fetch a fresh copy from the connected device?",
        )
        if confirm != QMessageBox.Yes:
            return

    worker = MobileAppFetchWorker(adb, serial, package_id, target)
    window._mobile_app_fetch_worker = worker
    worker.progress.connect(window._update_status)
    worker.finished_ok.connect(lambda summary: _mobile_fetch_finished(window, target, summary))
    worker.failed.connect(lambda message: _mobile_fetch_failed(window, message))
    window._focus_terminal(
        banner=(
            f"Extracting {package_id} to {target.name}…\n"
            "Keep the phone unlocked. If Android asks for USB debugging or file access, approve it."
        )
    )
    worker.start()


def _mobile_fetch_finished(window, target: Path, summary: dict) -> None:
    window._update_status(f"Mobile ROM created: {target}")
    if hasattr(window, "open_rom_path"):
        window.open_rom_path(str(target))


def _mobile_fetch_failed(window, message: str) -> None:
    QMessageBox.critical(window, "Mobile ROM extract failed", message)
    window._update_status("Mobile ROM extract failed.")


class MobileAppFetchWorker(QThread):
    progress = Signal(str)
    finished_ok = Signal(dict)
    failed = Signal(str)

    def __init__(self, adb: str, serial: str, package_id: str, target: Path):
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.package_id = package_id
        self.target = target

    def run(self) -> None:
        try:
            summary = fetch_android_app_rom(self.adb, self.serial, self.package_id, self.target, progress=self.progress.emit)
            self.finished_ok.emit(summary)
        except Exception as exc:
            self.failed.emit(str(exc))


def fetch_android_app_rom(adb: str, serial: str, package_id: str, target: Path, *, progress: Callable[[str], None]) -> dict:
    from ..platforms.mobile.archive import package_mobile_rom_directory

    adb_base = [adb, "-s", serial]
    progress(f"Checking package: {package_id}")
    package_paths = _package_paths(adb_base, package_id)
    if not package_paths:
        raise RuntimeError(f"Package not found on device: {package_id}")

    if target.exists():
        progress(f"Removing existing mobile ROM: {target}")
        _remove_mobile_rom_target(target)

    staging = Path(tempfile.mkdtemp(prefix=f"{_safe_app_name(package_id)}_", dir=target.parent))
    try:
        for sub in ("apk", "external_files", "external_cache", "obb", "candidates", "probe"):
            (staging / sub).mkdir(parents=True, exist_ok=True)

        app_name = _safe_app_name(package_id)
        manifest = {
            "format": "rae-mobile-app-rom-v1",
            "platform": "android",
            "packageId": package_id,
            "appName": app_name,
            "sourceLabel": f"Android app {package_id}",
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "deviceSerial": serial,
        }
        summary: dict = {
            **manifest,
            "target": str(target),
            "staging": str(staging),
            "apkSplits": [],
            "optionalPulls": {},
            "candidateFiles": [],
            "notes": [],
        }
        (staging / "rae_mobile_rom.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        (staging / "probe" / "apk_paths.txt").write_text("\n".join(package_paths) + "\n", encoding="utf-8")

        for remote in package_paths:
            local = staging / "apk" / Path(remote).name
            progress(f"Pulling APK split: {remote}")
            _adb_pull(adb_base, remote, local, progress=progress, optional=False)
            summary["apkSplits"].append({"remote": remote, "local": str(local)})

        _probe_listing(adb_base, f"/sdcard/Android/data/{package_id}", staging / "probe" / "android_data_listing.txt", progress)
        _probe_listing(adb_base, f"/sdcard/Android/obb/{package_id}", staging / "probe" / "obb_listing.txt", progress)

        optional_dirs = [
            (f"/sdcard/Android/data/{package_id}/files", staging / "external_files", "external files"),
            (f"/sdcard/Android/data/{package_id}/cache", staging / "external_cache", "external cache"),
            (f"/sdcard/Android/obb/{package_id}", staging / "obb", "OBB"),
        ]
        for remote, local, label in optional_dirs:
            progress(f"Pulling {label}: {remote}")
            ok = _adb_pull(adb_base, remote, local, progress=progress, optional=True)
            summary["optionalPulls"][label] = {"remote": remote, "local": str(local), "ok": ok}

        progress("Searching app external data for Unity/mobile candidate files…")
        candidates = _find_candidate_files(adb_base, package_id, progress)
        (staging / "probe" / "candidate_files.txt").write_text("\n".join(candidates) + ("\n" if candidates else ""), encoding="utf-8")
        summary["candidateFiles"] = candidates

        limit = 5000
        for remote in candidates[:limit]:
            rel = remote.removeprefix(f"/sdcard/Android/data/{package_id}/").lstrip("/")
            local = staging / "candidates" / rel
            local.parent.mkdir(parents=True, exist_ok=True)
            _adb_pull(adb_base, remote, local, progress=progress, optional=True)
        if len(candidates) > limit:
            note = f"candidate pull capped at {limit} files out of {len(candidates)}"
            progress(note)
            summary["notes"].append(note)

        _report_home_cache_health(staging, package_id, progress, summary)
        _write_readme(staging, package_id, app_name)
        _try_build_inventory(staging, package_id, progress, summary)
        (staging / "probe" / "fetch_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

        progress(f"Packaging mobile ROM archive: {target.name}")
        file_count = package_mobile_rom_directory(staging, target)
        summary["archiveFileCount"] = file_count
        progress(f"Mobile ROM archive ready: {target} ({file_count:,} file(s))")
        return summary
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _report_home_cache_health(staging: Path, package_id: str, progress: Callable[[str], None], summary: dict) -> None:
    """After pulling a Pokémon HOME ROM, confirm the readable Cache came along."""
    if "pokemonhome" not in package_id.replace(".", "").casefold():
        return
    cache_dir = staging / "external_files" / "files" / "Cache"
    cache_files = [p for p in cache_dir.rglob("*") if p.is_file()] if cache_dir.is_dir() else []
    if cache_files:
        total = sum(p.stat().st_size for p in cache_files)
        note = (
            f"HOME Cache captured: {len(cache_files):,} file(s), {total / 1_000_000:.1f} MB. "
            "Species you viewed in HOME will be previewable."
        )
    else:
        note = (
            "WARNING: no HOME Cache files were pulled. Open Pokémon HOME on the phone, view a few "
            "Pokémon (this downloads their models into the app cache), then extract again."
        )
    progress(note)
    summary["notes"].append(note)


def _remove_mobile_rom_target(target: Path) -> None:
    if not target.exists():
        return
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink()


def _connected_android_serial(adb: str, progress: Callable[[str], None]) -> str:
    devices = _run([adb, "devices", "-l"], check=True)
    rows: list[str] = []
    unauthorized: list[str] = []
    for line in devices.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices"):
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            rows.append(parts[0])
        elif len(parts) >= 2 and parts[1] == "unauthorized":
            unauthorized.append(parts[0])
    if not rows:
        if unauthorized:
            raise RuntimeError("Android device is connected but unauthorized. Unlock the phone and accept the USB debugging prompt, then try again.")
        raise RuntimeError("No authorized Android device found. Enable USB debugging, plug in the phone, unlock it, then try again.")
    if len(rows) > 1:
        progress(f"Multiple devices found; using first: {rows[0]}")
    return rows[0]


def _installed_packages(adb: str, serial: str) -> list[str]:
    cp = _run([adb, "-s", serial, "shell", "pm", "list", "packages", "-3"], check=False)
    if cp.returncode != 0 or not cp.stdout.strip():
        cp = _run([adb, "-s", serial, "shell", "pm", "list", "packages"], check=True)
    packages: list[str] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            packages.append(line.split(":", 1)[1].strip())
    return packages


def _package_paths(adb_base: list[str], package_id: str) -> list[str]:
    cp = _run(adb_base + ["shell", "pm", "path", package_id], check=False)
    paths: list[str] = []
    for line in cp.stdout.splitlines():
        line = line.strip()
        if line.startswith("package:"):
            paths.append(line.split(":", 1)[1].strip())
    return paths


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and cp.returncode != 0:
        detail = (cp.stderr or cp.stdout or "").strip()
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{detail}")
    return cp


def _adb_pull(adb_base: list[str], remote: str, local: Path, *, progress: Callable[[str], None], optional: bool) -> bool:
    local.parent.mkdir(parents=True, exist_ok=True)
    cp = _run(adb_base + ["pull", remote, str(local)], check=False)
    if cp.returncode == 0:
        return True
    detail = (cp.stderr or cp.stdout or "").strip()
    msg = f"Could not pull {remote}: {detail or 'adb pull failed'}"
    if optional:
        progress("Optional pull skipped: " + msg)
        return False
    raise RuntimeError(msg)


def _probe_listing(adb_base: list[str], remote: str, out: Path, progress: Callable[[str], None]) -> None:
    cp = _run(adb_base + ["shell", "ls", "-la", remote], check=False)
    out.write_text((cp.stdout or "") + ("\nSTDERR:\n" + cp.stderr if cp.stderr else ""), encoding="utf-8")
    if cp.returncode != 0:
        progress(f"Listing not available: {remote}")


def _find_candidate_files(adb_base: list[str], package_id: str, progress: Callable[[str], None]) -> list[str]:
    root = f"/sdcard/Android/data/{package_id}"
    cp = _run(adb_base + ["shell", "find", root, "-maxdepth", "8", "-type", "f"], check=False)
    if cp.returncode != 0 and not cp.stdout:
        progress("find did not return external data candidates; this can happen on some Android versions.")
        return []
    candidates: list[str] = []
    seen: set[str] = set()
    for line in cp.stdout.splitlines():
        path = line.strip()
        if not path.startswith("/"):
            continue
        if CANDIDATE_RE.search(path) and path not in seen:
            candidates.append(path)
            seen.add(path)
    progress(f"Candidate files found: {len(candidates):,}")
    return candidates


def _try_build_inventory(target: Path, package_id: str, progress: Callable[[str], None], summary: dict) -> None:
    try:
        from ..platforms.mobile.rom import scan_mobile_rom_path
    except Exception as exc:
        summary["notes"].append(f"Mobile ROM scanner not available yet: {exc}")
        return
    try:
        progress("Building RAE mobile ROM inventory…")
        assets = scan_mobile_rom_path(target, progress=progress)
        summary["raeAssetRows"] = len(assets)
        if package_id == POKEMON_HOME_PACKAGE:
            home_inventory = target / "home_inventory.json"
            if home_inventory.exists():
                summary["homeInventory"] = str(home_inventory)
        progress(f"Mobile ROM inventory ready: {len(assets):,} row(s)")
    except Exception as exc:
        summary["notes"].append(f"Mobile ROM inventory build failed: {exc}")
        progress(f"Mobile ROM inventory build failed, but fetch completed: {exc}")


def _write_readme(target: Path, package_id: str, app_name: str) -> None:
    (target / "README.md").write_text(
        f"# {app_name}.rom\n\n"
        f"This is a RAE mobile app ROM archive for `{package_id}`.\n\n"
        "It is a single `.rom` zip file. RAE unpacks it to a local cache while browsing.\n\n"
        "Contents may include installed APK splits, external Android files/cache, OBB files if present, and copied Unity/mobile candidate files.\n\n"
        "RAE does not decrypt protected packages, scrape servers, or bypass device/app protections.\n",
        encoding="utf-8",
    )


def _safe_app_name(package_id: str) -> str:
    if package_id in KNOWN_APP_ROM_NAMES:
        return KNOWN_APP_ROM_NAMES[package_id]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", package_id).strip("._-")
    return cleaned.replace(".", "_") or "android_app"


# RAE_DEVICE_TOOLKIT_FORCE_VISIBLE_INSTALLER
def install_mobile_device_toolkit(window) -> None:
    """Install the generic Device Toolkit menu on the main window.

    This definition intentionally overrides older patch definitions below/above it.
    It is idempotent: repeated calls reuse the existing menu and remove stale duplicate
    action rows before adding the current generic mobile ROM extractor.
    """
    from PySide6.QtGui import QAction

    menubar = window.menuBar()
    menu = _menu_named(menubar, "Device Toolkit") or menubar.addMenu("Device Toolkit")
    mobile_menu = _submenu_named(menu, "Mobile") or menu.addMenu("Mobile")

    for action in list(mobile_menu.actions()):
        if action.text().replace("&", "") in {
            "Fetch Pokémon HOME to roms/pokemon_home…",
            "Extract Installed App ROM…",
        }:
            mobile_menu.removeAction(action)

    extract_action = QAction("Extract Installed App ROM…", window)
    extract_action.triggered.connect(lambda: _extract_installed_app_rom(window))
    mobile_menu.addAction(extract_action)

    toolbar = getattr(window, "main_toolbar", None)
    if toolbar is not None:
        for action in toolbar.actions():
            if action.text().replace("&", "") == "Extract App ROM":
                toolbar.removeAction(action)
                break

