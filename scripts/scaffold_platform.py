#!/usr/bin/env python3
"""Scaffold a new RAE platform island from platforms/_template/.

Usage:
  python scripts/scaffold_platform.py switch "Nintendo Switch" --ext .nsp --ext .xci --active
  python scripts/scaffold_platform.py gba "Game Boy Advance" --ext .gba

Registers the platform in core/registry.py and wires asset magics when --active.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path


def _class_name(platform_id: str) -> str:
    parts = re.split(r"[_\-\s]+", platform_id.strip())
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


def _substitute(text: str, *, platform_id: str, platform_label: str, platform_class: str, rom_extensions: str, platform_status: str) -> str:
    return (
        text.replace("{{platform_id}}", platform_id)
        .replace("{{platform_label}}", platform_label)
        .replace("{{platform_class}}", platform_class)
        .replace("{{rom_extensions}}", rom_extensions)
        .replace("{{platform_status}}", platform_status)
    )


def _copy_template(src: Path, dest: Path, **subs: str) -> None:
    if dest.exists():
        raise SystemExit(f"Platform folder already exists: {dest}")
    shutil.copytree(src, dest)
    for path in dest.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".md"}:
            path.write_text(_substitute(path.read_text(encoding="utf-8"), **subs), encoding="utf-8")


def _register_registry(rae_root: Path, platform_id: str, platform_class: str) -> None:
    registry = rae_root / "src" / "core" / "registry.py"
    text = registry.read_text(encoding="utf-8")
    import_line = f"    from ..platforms.{platform_id} import {platform_class}_PLATFORM"
    if import_line not in text:
        anchor = "    from ..platforms.mobile import MOBILE_PLATFORM"
        if anchor not in text:
            raise SystemExit("Could not find registry import anchor")
        text = text.replace(anchor, f"{import_line}\n{anchor}")
    entry = f"            {platform_class}_PLATFORM,"
    if entry not in text:
        anchor = "            MOBILE_PLATFORM,"
        text = text.replace(anchor, f"{entry}\n{anchor}")
    registry.write_text(text, encoding="utf-8")


def _register_module_builder(rae_root: Path, platform_id: str) -> None:
    boundaries = rae_root / "src" / "core" / "modules" / "platform_boundaries.py"
    text = boundaries.read_text(encoding="utf-8")
    line = f'    "{platform_id}": "rae.platforms.{platform_id}.platform_modules:build_{platform_id}_modules",'
    if line not in text:
        anchor = '    "mobile": "rae.platforms.mobile.platform_modules:build_mobile_modules",'
        text = text.replace(anchor, f"{anchor}\n{line}")
    boundaries.write_text(text, encoding="utf-8")


def _register_asset_magics(rae_root: Path, platform_id: str) -> None:
    magics = rae_root / "src" / "core" / "modules" / "asset_magics.py"
    text = magics.read_text(encoding="utf-8")
    fn_name = f"_{platform_id}_magics"
    if fn_name in text:
        return
    loader = f"        {fn_name},"
    if loader not in text:
        text = text.replace("        _threeds_magics,", f"        _threeds_magics,\n{loader}")
    helper = f'''

def {fn_name}() -> dict[str, str]:
    from ...platforms.{platform_id}.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)
'''
    text = text.rstrip() + helper + "\n"
    magics.write_text(text, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new RAE platform island")
    parser.add_argument("platform_id", help="Short id, e.g. switch, gba")
    parser.add_argument("label", help='Human label, e.g. "Nintendo Switch"')
    parser.add_argument("--ext", action="append", dest="extensions", default=[], help="ROM extension, e.g. .gba (repeatable)")
    parser.add_argument("--active", action="store_true", help="Mark platform active and register module builder")
    args = parser.parse_args(argv)

    platform_id = args.platform_id.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_]*", platform_id):
        raise SystemExit("platform_id must be lowercase alphanumeric/underscore, starting with a letter")

    platform_class = _class_name(platform_id)
    exts = tuple(args.extensions) or (f".{platform_id}",)
    rom_extensions = "(" + ", ".join(repr(e) for e in exts) + ")"
    platform_status = "active" if args.active else "planned"

    rae_root = Path(__file__).resolve().parents[1]
    template = rae_root / "src" / "platforms" / "_template"
    dest = rae_root / "src" / "platforms" / platform_id

    subs = dict(
        platform_id=platform_id,
        platform_label=args.label,
        platform_class=platform_class,
        rom_extensions=rom_extensions,
        platform_status=platform_status,
    )
    _copy_template(template, dest, **subs)
    _register_registry(rae_root, platform_id, platform_class)
    _register_asset_magics(rae_root, platform_id)
    if args.active:
        _register_module_builder(rae_root, platform_id)

    print(f"Created platforms/{platform_id}/")
    print(f"  Class prefix: {platform_class}")
    print(f"  Status: {platform_status}")
    if args.active:
        print(f"  Registered PLATFORM_MODULE_BUILDERS['{platform_id}']")
    else:
        print("  Pass --active when scan/export modules are ready to wire the module builder.")
    print("  Next: implement rom.py scan, fill magics.py, run pytest tests/test_platform_import_isolation.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
