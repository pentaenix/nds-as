#!/usr/bin/env python3
"""Render 750x650 Models Resource preview icons with RAE's Three.js viewer."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

from rae.model_preview.web_snapshot import ModelWebSnapshotService
from rae.platforms.nds.export_module.building_icons import (
    render_models_resource_building_preview_icon,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--force", action="store_true", help="replace existing previews")
    parser.add_argument("--capture-width", type=int, default=1000)
    parser.add_argument("--capture-height", type=int, default=867)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--pitch", type=float, default=27.0)
    parser.add_argument("--zoom", type=float, default=0.82)
    return parser.parse_args()


def _camera_for_row(
    row: dict[str, object],
    overrides: dict[str, object],
    args: argparse.Namespace,
) -> tuple[float, float, float]:
    icon_stem = Path(str(row["icon"])).stem
    camera = overrides.get(icon_stem) or {}
    if not isinstance(camera, dict):
        camera = {}
    return (
        float(camera.get("yawDegrees", args.yaw)),
        float(camera.get("pitchDegrees", args.pitch)),
        float(camera.get("zoomFactor", args.zoom)),
    )


def main() -> int:
    args = _arguments()
    export_dir = args.export_dir.resolve()
    catalog_path = export_dir / "submission_catalog.json"
    override_path = export_dir / "icon_camera_overrides.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    override_data = (
        json.loads(override_path.read_text(encoding="utf-8"))
        if override_path.is_file()
        else {}
    )
    overrides = override_data.get("icons") or {}
    rows = list(catalog.get("files") or [])

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    service = ModelWebSnapshotService()
    if not service.begin_session(host):
        raise RuntimeError("RAE's Three.js/WebEngine previewer is unavailable")
    service.set_viewport_size(args.capture_width, args.capture_height)

    written = 0
    errors: list[dict[str, str]] = []
    cameras_used: dict[str, dict[str, float]] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="rae_models_resource_hd_capture_") as name:
            capture_input = Path(name) / "input"
            capture_input.mkdir()
            staged_glb = capture_input / "model.glb"

            for index, row in enumerate(rows, 1):
                package = export_dir / str(row["package"])
                source = export_dir / str(row["preview"])
                base = package.name.removesuffix("_asset.zip")
                output = package.with_name(f"{base}_preview.png")
                yaw, pitch, zoom = _camera_for_row(row, overrides, args)
                print(
                    f"Rendering preview {index}/{len(rows)}: {base} "
                    f"(yaw={yaw:g}, pitch={pitch:g}, zoom={zoom:g})",
                    flush=True,
                )
                try:
                    if not package.is_file() or not source.is_file():
                        raise FileNotFoundError("matching ZIP or preview GLB is unavailable")

                    def snapshot(candidate: Path) -> bytes | None:
                        shutil.copyfile(candidate, staged_glb)
                        return service.capture_blocking(
                            staged_glb,
                            args.capture_width,
                            args.capture_height,
                            yaw_deg=yaw,
                            pitch_deg=pitch,
                            zoom_factor=zoom,
                        )

                    if args.force or not output.is_file():
                        image = render_models_resource_building_preview_icon(
                            source,
                            package,
                            snapshot,
                        )
                        if image.size != (750, 650) or image.mode != "RGBA":
                            raise RuntimeError("renderer returned an invalid preview image")
                        image.save(output, format="PNG", optimize=True)
                    row["previewIcon"] = str(output.relative_to(export_dir))
                    cameras_used[output.name] = {
                        "yawDegrees": yaw,
                        "pitchDegrees": pitch,
                        "zoomFactor": zoom,
                    }
                    written += 1
                except Exception as exc:
                    errors.append({"package": str(row.get("package") or ""), "error": str(exc)})
    finally:
        service.end_session()
        app.processEvents()

    result = {
        "format": "PNG",
        "width": 750,
        "height": 650,
        "transparentBackground": True,
        "renderer": "three.js",
        "camera": "three-quarter with per-model overrides",
        "rendered": written,
        "errors": errors,
    }
    catalog["largePreviewIcons"] = result
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    report = {**result, "cameras": cameras_used}
    (export_dir / "large_preview_icon_report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2))
    return 0 if not errors and written == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
