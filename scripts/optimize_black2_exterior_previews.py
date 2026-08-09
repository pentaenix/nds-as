#!/usr/bin/env python3
"""Strip animations and safely optimize Black 2 exterior preview GLBs."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("exports/models_resource/pokemon_black_2"),
    )
    parser.add_argument("--apply", action="store_true", help="replace validated previews")
    parser.add_argument("--limit", type=int, default=0, help="process only the first N previews")
    return parser.parse_args()


def stats(path: Path) -> dict[str, int]:
    from rae.platforms.nds.gltf.glb_io import read_glb

    glb = read_glb(path)
    data = glb.json
    primitives = [primitive for mesh in data.get("meshes", []) for primitive in mesh.get("primitives", [])]
    triangles = sum(
        int(data["accessors"][primitive["indices"]].get("count", 0)) // 3
        for primitive in primitives
        if isinstance(primitive.get("indices"), int)
    )
    return {
        "bytes": path.stat().st_size,
        "triangles": triangles,
        "drawCalls": len(primitives),
        "materials": len(data.get("materials", [])),
        "images": len(data.get("images", [])),
        "animations": len(data.get("animations", [])),
    }


def validate(before: dict[str, int], after: dict[str, int]) -> None:
    minimum_triangles = int(before["triangles"] * 0.95)
    if after["triangles"] > before["triangles"] or after["triangles"] < minimum_triangles:
        raise RuntimeError("triangle count changed by more than the degenerate-triangle allowance")
    if before["images"] and not after["images"]:
        raise RuntimeError("all embedded textures were removed")
    if after["animations"]:
        raise RuntimeError("animations remain in optimized preview")
    if after["bytes"] >= before["bytes"]:
        raise RuntimeError("optimized preview is not smaller")


def main() -> int:
    args = arguments()
    root = args.root.resolve()
    gltfpack = shutil.which("gltfpack")
    if not gltfpack:
        raise FileNotFoundError("gltfpack is not installed or not on PATH")
    previews = sorted((root / "Maps" / "Locations").glob("*/*_preview.glb"))
    if args.limit > 0:
        previews = previews[: args.limit]
    backup = root.parent / f"{root.name}_exterior_glb_backup_{datetime.now():%Y%m%d_%H%M%S}"
    report: list[dict[str, object]] = []

    from rae.platforms.nds.gltf.glb_io import read_glb
    from rae.platforms.nds.gltf.merge_animations import strip_all_animations

    for index, source in enumerate(previews, 1):
        row: dict[str, object] = {"path": str(source.relative_to(root))}
        try:
            before = stats(source)
            with tempfile.TemporaryDirectory(prefix="rae_map_glb_opt_") as temp_name:
                temp = Path(temp_name)
                static = temp / "static.glb"
                optimized = temp / "optimized.glb"
                strip_all_animations(read_glb(source)).write(static)
                process = subprocess.run(
                    [gltfpack, "-i", str(static), "-o", str(optimized), "-noq", "-af", "0", "-ke", "-mm"],
                    text=True,
                    capture_output=True,
                )
                if process.returncode or not optimized.is_file():
                    raise RuntimeError((process.stderr or process.stdout or "gltfpack failed").strip())
                after = stats(optimized)
                validate(before, after)
                if args.apply:
                    saved = backup / source.relative_to(root)
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, saved)
                    replacement = source.with_suffix(".optimized.tmp")
                    shutil.copy2(optimized, replacement)
                    os.replace(replacement, source)
            row.update({"status": "optimized" if args.apply else "ready", "before": before, "after": after})
            print(
                f"[{index}/{len(previews)}] {source.parent.name}: "
                f"{before['drawCalls']}→{after['drawCalls']} draws, "
                f"{before['bytes'] // 1024}→{after['bytes'] // 1024} KiB"
            )
        except Exception as exc:
            row.update({"status": "skipped", "error": str(exc)})
            print(f"[{index}/{len(previews)}] SKIPPED {source.parent.name}: {exc}")
        report.append(row)

    report_path = root / "exterior_preview_optimization_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    optimized_count = sum(row["status"] in {"ready", "optimized"} for row in report)
    print(f"Validated {optimized_count}/{len(report)} previews. Report: {report_path}")
    if args.apply and optimized_count:
        print(f"Original GLBs backed up to: {backup}")
    return 0 if optimized_count == len(report) else 1


if __name__ == "__main__":
    raise SystemExit(main())
