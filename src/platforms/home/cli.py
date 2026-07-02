from __future__ import annotations

import json
from pathlib import Path

from .library import build_home_library


def dispatch_home(args) -> int:
    cmd = getattr(args, "home_cmd", None)
    if cmd in {None, "scan"}:
        return cmd_scan(args)
    if cmd == "list":
        return cmd_list(args)
    if cmd == "report":
        return cmd_report(args)
    return 2


def cmd_scan(args) -> int:
    lib = build_home_library(args.source, progress=print, inspect_unity=not args.no_unity)
    out = Path(args.out)
    lib.write_json(out)
    print(f"Wrote HOME library inventory: {out}")
    print(_summary(lib.to_dict()))
    return 0


def cmd_list(args) -> int:
    lib = build_home_library(args.source, progress=None, inspect_unity=not args.no_unity)
    for pkg in lib.packages:
        s = pkg.status
        print(
            f"{pkg.id:<14} {pkg.name:<18} "
            f"model:{s.get('model','?'):<7} tex:{s.get('textures','?'):<7} "
            f"rig:{s.get('skeletonRig','?'):<7} anim:{s.get('animations','?'):<7} "
            f"idle:{s.get('idle','?'):<7} phys:{s.get('physicalAttack','?'):<7} special:{s.get('specialAttack','?'):<7} "
            f"files:{len(pkg.source_files):>3} objects:{sum(pkg.object_counts.values()):>4}"
        )
    print(_summary(lib.to_dict()))
    return 0


def cmd_report(args) -> int:
    data = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    print(_summary(data))
    return 0


def _summary(data: dict) -> str:
    packages = data.get("packages", [])
    complete_model = 0
    complete_all3 = 0
    with_anim = 0
    for pkg in packages:
        status = pkg.get("status", {})
        if status.get("model") == "found" and status.get("textures") == "found" and status.get("skeletonRig") == "found":
            complete_model += 1
        if status.get("animations") == "found":
            with_anim += 1
        if all(status.get(k) == "found" for k in ("idle", "physicalAttack", "specialAttack")):
            complete_all3 += 1
    return (
        f"HOME packages: {len(packages):,}\n"
        f"model+texture+rig found: {complete_model:,}\n"
        f"with any animation evidence: {with_anim:,}\n"
        f"with idle+physical+special candidates: {complete_all3:,}\n"
        f"UnityPy available: {data.get('unityPyAvailable')}"
    )
