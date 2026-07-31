#!/usr/bin/env python3
"""Render Models Resource icons with RAE's EasyFind Three.js previewer."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

from rae.model_preview.web_snapshot import ModelWebSnapshotService
from rae.platforms.nds.export_module.building_icons import (
    render_models_resource_building_icons,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dae_export_dir", type=Path)
    parser.add_argument("glb_export_dir", type=Path)
    parser.add_argument("--force", action="store_true", help="replace existing icons")
    parser.add_argument("--width", type=int, default=512)
    parser.add_argument("--height", type=int, default=432)
    parser.add_argument("--zoom", type=float, default=0.82)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--pitch", type=float, default=27.0)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    service = ModelWebSnapshotService()
    if not service.begin_session(host):
        raise RuntimeError("RAE's Three.js/WebEngine previewer is unavailable")
    service.set_viewport_size(args.width, args.height)

    try:
        with tempfile.TemporaryDirectory(prefix="rae_models_resource_capture_") as name:
            capture_input = Path(name) / "input"
            capture_input.mkdir()
            staged_glb = capture_input / "model.glb"

            def snapshot(source: Path) -> bytes | None:
                # Exported GLBs are self-contained. Giving the service a one-file
                # directory avoids restaging the complete 668-model catalog for
                # every WYSIWYG capture.
                shutil.copyfile(source, staged_glb)
                return service.capture_blocking(
                    staged_glb,
                    args.width,
                    args.height,
                    yaw_deg=args.yaw,
                    pitch_deg=args.pitch,
                    zoom_factor=args.zoom,
                )

            result = render_models_resource_building_icons(
                args.dae_export_dir,
                args.glb_export_dir,
                progress=lambda message: print(message, flush=True),
                force=args.force,
                snapshot_renderer=snapshot,
            )
    finally:
        service.end_session()
        app.processEvents()

    print(json.dumps(result, indent=2))
    return 0 if not result.get("errors") else 1


if __name__ == "__main__":
    raise SystemExit(main())
