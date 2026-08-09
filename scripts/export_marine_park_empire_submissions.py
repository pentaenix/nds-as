#!/usr/bin/env python3
"""Export categorized Marine Park Empire Models Resource submissions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import tempfile

from PySide6.QtWidgets import QApplication, QWidget

from rae.model_preview.web_snapshot import ModelWebSnapshotService
from rae.platforms.windows_iso.bulk_submission import (
    CATEGORY_FOLDERS,
    build_submission_jobs,
    export_submission_jobs,
)
from rae.platforms.windows_iso.rom import load_descriptor, scan_windows_iso_rom_path


_REPO_ROOT = Path(__file__).resolve().parents[1]


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rom",
        type=Path,
        default=_REPO_ROOT / "roms/Marine Park Empire 2005 PREACTIVATED-ASPM.iso",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_REPO_ROOT / "exports/Marine Park Empire",
    )
    parser.add_argument(
        "--category",
        action="append",
        choices=CATEGORY_FOLDERS,
        help="export only this category; repeat to select more than one",
    )
    parser.add_argument("--limit", type=int, help="export only the first N selected jobs")
    parser.add_argument(
        "--start-index", type=int, default=0,
        help="skip this many selected jobs (useful for disjoint parallel chunks)",
    )
    parser.add_argument("--force", action="store_true", help="replace complete submissions")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--pitch", type=float, default=27.0)
    parser.add_argument("--zoom", type=float, default=0.82)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    assets = scan_windows_iso_rom_path(
        args.rom,
        progress=lambda message: print(message, flush=True),
        cache_root=_REPO_ROOT / ".cache/windows_iso",
    )
    descriptors = [load_descriptor(asset) for asset in assets]
    jobs = build_submission_jobs(
        descriptor for descriptor in descriptors if descriptor.get("type") == "model"
    )
    if args.category:
        selected = set(args.category)
        jobs = [job for job in jobs if job.category in selected]
    jobs = jobs[max(0, args.start_index):]
    if args.limit is not None:
        jobs = jobs[:max(0, args.limit)]
    print(f"Prepared {len(jobs):,} deduplicated submission jobs.", flush=True)

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    service = ModelWebSnapshotService()
    if not service.begin_session(host):
        raise RuntimeError("RAE's Three.js/WebEngine previewer is unavailable")
    service.set_viewport_size(args.width, args.height)
    widget = getattr(service, "_widget", None)
    if widget is not None:
        widget.set_preview_platform("windows_iso")
        widget.wait_until_api_ready()

    try:
        with tempfile.TemporaryDirectory(prefix="rae_mpe_bulk_capture_") as temp_name:
            staged_glb = Path(temp_name) / "model.glb"

            def snapshot(source: Path) -> bytes | None:
                shutil.copyfile(source, staged_glb)
                return service.capture_blocking(
                    staged_glb,
                    args.width,
                    args.height,
                    yaw_deg=args.yaw,
                    pitch_deg=args.pitch,
                    zoom_factor=args.zoom,
                )

            category_report = "bulk_export_report.json"
            if args.category:
                label = "_".join(value.replace(" ", "_") for value in args.category)
                if args.start_index:
                    label = f"{label}_from_{args.start_index}"
                category_report = f"bulk_export_report_{label}.json"
            report = export_submission_jobs(
                jobs,
                args.output,
                snapshot,
                force=args.force,
                report_filename=category_report,
                progress=lambda message: print(message, flush=True),
            )
    finally:
        service.end_session()
        app.processEvents()
    print(json.dumps({
        "total": report["total"],
        "exported": len(report["exported"]),
        "skipped": len(report["skipped"]),
        "errors": len(report["errors"]),
        "output": str(args.output.resolve()),
    }, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
