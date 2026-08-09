#!/usr/bin/env python3
"""Audit or quarantine exact duplicate Black 2 Models Resource map packages."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from rae.platforms.nds.export_module.models_resource_map_dedup import (
    apply_exact_deduplication,
    discover_complete_packages,
    find_exact_duplicate_groups,
    report_rows,
)


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("exports/models_resource/pokemon_black_2"),
        help="completed Black 2 Models Resource export root",
    )
    parser.add_argument("--apply", action="store_true", help="quarantine duplicates and disable their review rows")
    parser.add_argument("--quarantine", type=Path, help="optional explicit quarantine directory")
    return parser.parse_args()


def main() -> int:
    args = arguments()
    output = args.output.resolve()
    packages = discover_complete_packages(output)
    groups = find_exact_duplicate_groups(packages)
    print(json.dumps({
        "output": str(output),
        "complete_packages": len(packages),
        "duplicate_groups": len(groups),
        "redundant_packages": sum(len(group.duplicates) for group in groups),
        "groups": report_rows(groups),
    }, indent=2))
    if not args.apply:
        print("Dry run only. Pass --apply to quarantine the reported duplicates.")
        return 0
    quarantine, moved, review_keys = apply_exact_deduplication(
        output,
        groups,
        quarantine_root=args.quarantine.resolve() if args.quarantine else None,
    )
    print(f"Quarantined {len(moved)} package(s) at {quarantine}")
    print(f"Disabled {len(review_keys)} matching map_review.csv row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
