#!/usr/bin/env python3
"""Export full Pokémon Black 2 locations as Models Resource-ready packages."""
from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


def _default_rom() -> Path:
    return Path("roms/Pokemon - Black Version 2 (USA, Europe) (NDSi Enhanced).nds")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rom", type=Path, default=_default_rom())
    parser.add_argument("--output", type=Path, default=Path("exports/models_resource/pokemon_black_2"))
    parser.add_argument("--review", type=Path, help="edited map_review.csv; defaults to OUTPUT/map_review.csv")
    parser.add_argument("--catalog-only", action="store_true", help="write the review catalog without exporting models")
    parser.add_argument("--section", action="append", choices=("maps", "interiors"), help="limit export; repeat for both")
    parser.add_argument("--title", action="append", help="export an exact reviewed title; repeat for several")
    parser.add_argument("--limit", type=int, default=0, help="maximum submissions for a test run; 0 means all")
    parser.add_argument("--start-index", type=int, default=1, help="1-based index after filtering")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace packages; a complete run removes the selected generated section folders first",
    )
    parser.add_argument("--fresh", action="store_true", help="archive existing map package folders before a full rebuild")
    parser.add_argument("--images-only", action="store_true", help="rerender icons/previews for completed packages only")
    parser.add_argument(
        "--retry-errors",
        action="store_true",
        help="export only keys listed in OUTPUT/map_export_errors.json",
    )
    parser.add_argument("--width", type=int, default=1100)
    parser.add_argument("--height", type=int, default=850)
    parser.add_argument("--yaw", type=float, default=12.0)
    parser.add_argument("--pitch", type=float, default=35.0)
    parser.add_argument("--zoom", type=float, default=0.78)
    return parser.parse_args()


def main() -> int:
    args = arguments()
    rom = args.rom.resolve()
    output = args.output.resolve()
    if not rom.is_file():
        raise FileNotFoundError(f"ROM not found: {rom}")

    from rae.platforms.nds.export_module.models_resource_map_catalog import (
        apply_review,
        build_black2_map_catalog,
        write_review_files,
    )

    print("Reading Black 2 map topology and official location names…", flush=True)
    submissions, rom_files = build_black2_map_catalog(rom)
    catalog_path, default_review = write_review_files(submissions, output)
    review_path = args.review.resolve() if args.review else default_review
    submissions = apply_review(submissions, review_path)
    sections = set(args.section or ("maps", "interiors"))
    wanted = {
        "Maps" if section == "maps" else "Interior Maps"
        for section in sections
    }
    submissions = [item for item in submissions if item.section in wanted]
    if args.title:
        requested_titles = set(args.title)
        submissions = [item for item in submissions if item.title in requested_titles]
        missing_titles = sorted(requested_titles - {item.title for item in submissions})
        if missing_titles:
            raise ValueError(f"Reviewed map title(s) not found: {', '.join(missing_titles)}")
    retry_report = output / "map_export_errors.json"
    if args.retry_errors:
        if not retry_report.is_file():
            raise FileNotFoundError(f"Error report not found: {retry_report}")
        retry_keys = {
            str(row.get("key", ""))
            for row in json.loads(retry_report.read_text(encoding="utf-8"))
            if row.get("key")
        }
        submissions = [item for item in submissions if item.key in retry_keys]
        if not submissions:
            print("No reviewed submissions remain in the error report.", flush=True)
            return 0
    start = max(0, args.start_index - 1)
    submissions = submissions[start:]
    if args.limit > 0:
        submissions = submissions[:args.limit]
    summary = {
        "catalog": str(catalog_path), "review": str(review_path),
        "selected": len(submissions),
        "maps": sum(item.section == "Maps" for item in submissions),
        "interiors": sum(item.section == "Interior Maps" for item in submissions),
    }
    print(json.dumps(summary, indent=2), flush=True)
    if args.catalog_only:
        print("Edit map_review.csv to rename, skip, or reclassify ambiguous interiors, then rerun without --catalog-only.")
        return 0
    complete_for_selected_sections = (
        args.limit == 0 and args.start_index == 1 and not args.retry_errors and not args.title
    )
    if args.force and complete_for_selected_sections and not args.fresh and not args.images_only:
        removed = []
        for section_name in sorted(wanted):
            source = output / section_name
            if source.exists():
                shutil.rmtree(source)
                removed.append(section_name)
        if removed:
            print(f"Removed previous generated {' and '.join(removed)} folders for a clean forced rebuild.", flush=True)
    if args.fresh:
        if args.images_only:
            raise ValueError("--fresh cannot be combined with --images-only")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = output.parent / f"{output.name}_backup_{timestamp}"
        moved = []
        for section_name in ("Maps", "Interior Maps"):
            source = output / section_name
            if source.exists():
                backup.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(backup / section_name))
                moved.append(section_name)
        if moved:
            print(f"Archived previous {' and '.join(moved)} packages to {backup}", flush=True)

    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QWidget
    from rae.model_preview.web_snapshot import ModelWebSnapshotService
    from rae.platforms.nds.export_module.models_resource_maps import (
        export_map_submission,
        rerender_map_submission_images,
    )

    app = QApplication.instance() or QApplication([])
    host = QWidget()
    service = ModelWebSnapshotService()
    if not service.begin_session(host):
        raise RuntimeError("RAE's Three.js/WebEngine previewer is unavailable")
    service.set_viewport_size(args.width, args.height)
    errors: list[dict[str, str]] = []
    try:
        with tempfile.TemporaryDirectory(prefix="rae_black2_map_capture_") as name:
            staged = Path(name) / "map.glb"

            def snapshot(source: Path) -> bytes | None:
                shutil.copy2(source, staged)
                return service.capture_blocking(
                    staged, args.width, args.height, yaw_deg=args.yaw,
                    pitch_deg=args.pitch, zoom_factor=args.zoom,
                )

            for index, submission in enumerate(submissions, 1):
                try:
                    if args.images_only:
                        rendered = rerender_map_submission_images(
                            output, submission, snapshot_renderer=snapshot,
                            progress=lambda message: print(f"  {message}", flush=True),
                        )
                        if rendered:
                            print(f"[{index}/{len(submissions)}] Updated: {submission.title}", flush=True)
                    else:
                        print(f"[{index}/{len(submissions)}] {submission.section}: {submission.title}", flush=True)
                        export_map_submission(
                            rom, output, submission, rom_files, snapshot_renderer=snapshot,
                            progress=lambda message: print(f"  {message}", flush=True), force=args.force,
                        )
                except Exception as exc:
                    errors.append({"key": submission.key, "title": submission.title, "error": str(exc)})
                    print(f"  ERROR: {exc}", flush=True)
    finally:
        service.end_session()
        host.close()
        host.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    report = output / "map_export_errors.json"
    report.write_text(json.dumps(errors, indent=2) + "\n", encoding="utf-8")
    print(f"Finished with {len(errors)} error(s). Report: {report}")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
