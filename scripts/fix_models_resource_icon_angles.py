#!/usr/bin/env python3
"""Interactively rerender individual Models Resource icons at absolute yaw angles."""
from __future__ import annotations

import argparse
import json
import shlex
import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

from rae.model_preview.web_snapshot import ModelWebSnapshotService
from rae.platforms.nds.export_module.building_icons import (
    render_models_resource_building_icon,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_DAE_DIR = _REPO_ROOT / "exports/buildings/black_2_models_resource_dae"
_DEFAULT_GLB_DIR = _REPO_ROOT / "exports/buildings/black_2_all_unique"
_OVERRIDE_FILE = "icon_camera_overrides.json"


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dae-export-dir", type=Path, default=_DEFAULT_DAE_DIR)
    parser.add_argument("--glb-export-dir", type=Path, default=_DEFAULT_GLB_DIR)
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=432)
    parser.add_argument("--pitch", type=float, default=27.0)
    parser.add_argument("--zoom", type=float, default=0.82)
    return parser.parse_args()


def _normalized_icon_stem(value: str) -> str:
    name = Path(value.strip()).name
    if name.casefold().endswith(".png"):
        name = name[:-4]
    return name


def _parse_command(line: str) -> tuple[str, float]:
    fields = shlex.split(line)
    if len(fields) != 2:
        raise ValueError("expected: ICON_NAME ANGLE")
    icon_stem = _normalized_icon_stem(fields[0])
    if not icon_stem:
        raise ValueError("icon name is empty")
    try:
        angle = float(fields[1])
    except ValueError as exc:
        raise ValueError("angle must be a number from -359 through 360") from exc
    if not -359.0 <= angle <= 360.0:
        raise ValueError("angle must be from -359 through 360")
    if angle == 360.0:
        angle = 0.0
    return icon_stem, angle


def _load_catalogs(
    dae_export_dir: Path,
    glb_export_dir: Path,
) -> tuple[dict[str, dict[str, object]], dict[str, Path]]:
    dae_catalog = json.loads(
        (dae_export_dir / "submission_catalog.json").read_text(encoding="utf-8")
    )
    glb_catalog = json.loads(
        (glb_export_dir / "building_catalog.json").read_text(encoding="utf-8")
    )
    rows_by_icon = {
        Path(str(row["icon"])).stem: row
        for row in dae_catalog.get("files") or []
        if row.get("icon")
    }
    glb_by_hash = {
        str(row["sha256"]): glb_export_dir / str(row["file"])
        for row in glb_catalog.get("files") or []
    }
    return rows_by_icon, glb_by_hash


def _record_override(
    path: Path,
    icon_stem: str,
    *,
    yaw: float,
    pitch: float,
    zoom: float,
) -> None:
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
    else:
        data = {"icons": {}}
    icons = data.setdefault("icons", {})
    icons[icon_stem] = {
        "yawDegrees": yaw,
        "pitchDegrees": pitch,
        "zoomFactor": zoom,
    }
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = _arguments()
    dae_export_dir = args.dae_export_dir.resolve()
    glb_export_dir = args.glb_export_dir.resolve()
    rows_by_icon, glb_by_hash = _load_catalogs(dae_export_dir, glb_export_dir)

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    service = ModelWebSnapshotService()
    if not service.begin_session(host):
        raise RuntimeError("RAE's Three.js/WebEngine previewer is unavailable")
    service.set_viewport_size(args.width, args.height)

    print("Models Resource icon angle fixer")
    print("Enter: ICON_NAME ANGLE")
    print("Angles are absolute from front. Examples: -30, 180, 360 (front).")
    print("Enter 'quit' or press Ctrl-D to finish.\n")

    try:
        with tempfile.TemporaryDirectory(prefix="rae_icon_angle_fixer_") as name:
            capture_input = Path(name) / "input"
            capture_input.mkdir()
            staged_glb = capture_input / "model.glb"

            while True:
                try:
                    line = input("icon-angle> ").strip()
                except EOFError:
                    print()
                    break
                except KeyboardInterrupt:
                    print("\nEnter 'quit' to close, or provide another icon.")
                    continue
                if not line:
                    continue
                if line.casefold() in {"quit", "exit"}:
                    break
                if line.casefold() == "help":
                    print("Example: tship03_15_icon -30")
                    continue

                try:
                    icon_stem, yaw = _parse_command(line)
                    row = rows_by_icon.get(icon_stem)
                    if row is None:
                        raise ValueError(f"unknown icon: {icon_stem}")
                    source = glb_by_hash.get(str(row["sha256"]))
                    if source is None or not source.is_file():
                        raise FileNotFoundError("matching GLB source is unavailable")
                    package = dae_export_dir / str(row["package"])
                    icon = dae_export_dir / str(row["icon"])

                    def snapshot(candidate: Path) -> bytes | None:
                        shutil.copyfile(candidate, staged_glb)
                        return service.capture_blocking(
                            staged_glb,
                            args.width,
                            args.height,
                            yaw_deg=yaw,
                            pitch_deg=args.pitch,
                            zoom_factor=args.zoom,
                        )

                    image = render_models_resource_building_icon(
                        source,
                        package,
                        snapshot,
                    )
                    image.save(icon, format="PNG", optimize=True)
                    _record_override(
                        dae_export_dir / _OVERRIDE_FILE,
                        icon_stem,
                        yaw=yaw,
                        pitch=args.pitch,
                        zoom=args.zoom,
                    )
                    print(f"Updated {icon.name} at yaw {yaw:g}°")
                except Exception as exc:
                    print(f"Error: {exc}")
    finally:
        service.end_session()
        app.processEvents()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
