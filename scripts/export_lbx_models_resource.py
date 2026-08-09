#!/usr/bin/env python3
"""Export categorized LBX Models Resource submissions."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QWidget

from rae.install import project_root
from rae.model_preview.web_snapshot import ModelWebSnapshotService
from rae.platforms.threeds.lbx_catalog import LbxCatalog
from rae.platforms.threeds.lbx_submission import export_lbx_jobs, is_lbx_upright_weapon


def _arguments() -> argparse.Namespace:
    root = project_root()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rom",
        type=Path,
        default=root / "roms/LBX - Little Battlers eXperience (USA) (En,Fr,Es).cci",
    )
    parser.add_argument("--output", type=Path, default=root / "exports")
    parser.add_argument(
        "--category",
        action="append",
        choices=("chips", "parts", "weapons"),
        help="export only this top-level category; repeat to select more than one",
    )
    parser.add_argument(
        "--subcategory",
        action="append",
        help="export only matching subcategories, for example Body or Left Arm",
    )
    parser.add_argument(
        "--upright-weapons-only",
        action="store_true",
        help="export only shields and claws using their upright, front-facing profile",
    )
    parser.add_argument(
        "--romfs-prefix",
        help="export only one RomFS directory, for example /3ddata/coreparts/",
    )
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--force-subcategory",
        action="append",
        help="rebuild this subcategory while preserving other completed packages",
    )
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--width", type=int, default=900)
    parser.add_argument("--height", type=int, default=760)
    parser.add_argument("--yaw", type=float, default=30.0)
    parser.add_argument("--pitch", type=float, default=27.0)
    parser.add_argument(
        "--chips-pitch",
        type=float,
        default=43.0,
        help="camera pitch used for Chips; defaults to 43 degrees",
    )
    parser.add_argument(
        "--parts-yaw",
        type=float,
        default=14.0,
        help="camera yaw used for Parts; defaults to 14 degrees",
    )
    parser.add_argument(
        "--parts-pitch",
        type=float,
        default=15.0,
        help="camera pitch used for Parts; defaults to 15 degrees",
    )
    parser.add_argument("--zoom", type=float, default=0.82)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    catalog = LbxCatalog(args.rom)
    jobs = catalog.build_jobs(romfs_prefix=args.romfs_prefix)
    if args.category:
        selected = {value.casefold() for value in args.category}
        jobs = [job for job in jobs if job.category[0].casefold() in selected]
    if args.subcategory:
        selected = {value.casefold() for value in args.subcategory}
        jobs = [job for job in jobs if len(job.category) > 1 and job.category[1].casefold() in selected]
    if args.upright_weapons_only:
        jobs = [job for job in jobs if is_lbx_upright_weapon(job)]
    jobs = jobs[max(0, args.start_index):]
    if args.limit is not None:
        jobs = jobs[:max(0, args.limit)]
    print(f"Prepared {len(jobs):,} LBX submission jobs.", flush=True)
    if args.plan_only:
        print(json.dumps([
            {
                "source": job.romfs_path,
                "output": str(job.relative_directory),
                "custom_r": job.custom_r_path,
                "auxiliary": list(job.auxiliary_paths),
                "aliases": list(job.aliases),
            }
            for job in jobs
        ], indent=2))
        return 0

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    snapshots = ModelWebSnapshotService()
    if not snapshots.begin_session(host):
        raise RuntimeError("RAE's Three.js/WebEngine previewer is unavailable")
    snapshots.set_viewport_size(args.width, args.height)
    widget = getattr(snapshots, "_widget", None)
    if widget is not None:
        widget.set_preview_platform("3ds")
        widget.wait_until_api_ready()

    def snapshot(path: Path, job) -> bytes | None:
        top_level = job.category[0].casefold()
        if is_lbx_upright_weapon(job):
            yaw, pitch = 0.0, 0.0
        else:
            yaw = args.parts_yaw if top_level == "parts" else args.yaw
            pitch = (
                args.chips_pitch if top_level == "chips"
                else args.parts_pitch if top_level == "parts"
                else args.pitch
            )
        return snapshots.capture_blocking(
            path,
            args.width,
            args.height,
            yaw_deg=yaw,
            pitch_deg=pitch,
            zoom_factor=args.zoom,
        )

    try:
        report = export_lbx_jobs(
            catalog,
            jobs,
            args.output,
            snapshot,
            force=args.force,
            force_job=(
                lambda job: len(job.category) > 1
                and job.category[1].casefold()
                in {value.casefold() for value in args.force_subcategory}
            ) if args.force_subcategory else None,
            progress=lambda message: print(message, flush=True),
        )
    finally:
        snapshots.end_session()
        app.processEvents()
    output = args.output if args.output.name.casefold() == "lbx" else args.output / "LBX"
    print(json.dumps({
        "total": report["total"],
        "exported": len(report["exported"]),
        "skipped": len(report["skipped"]),
        "errors": len(report["errors"]),
        "output": str(output.resolve()),
    }, indent=2))
    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
