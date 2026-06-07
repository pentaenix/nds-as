from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .exporter import convert_texture_with_apicula, convert_with_apicula, export_assets, export_readable_asset, texture_outputs
from .nds import NDSRom
from .profiles import detect_profile
from .mapping import choose_mapping, mapping_summary
from .nitro_names import extract_nitro_names, texture_name_matches
from .scanner import Asset, filter_assets, scan_nds_path
from .util import human_size


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dsas", description="NDS-AS: Nintendo DS asset studio/exporter for local .nds files")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("run", help="Open the desktop UI")
    p_install = sub.add_parser("install", help="Create/refresh .venv, requirements, NDS-AS editable install, safe folders, and optional apicula")
    p_install.add_argument("--no-apicula", action="store_true", help="Skip optional apicula clone/build")

    p_maps = sub.add_parser("mappings", help="List available community mapping files")

    p_info = sub.add_parser("info", help="Show ROM info and DS/Pokémon scan hints")
    p_info.add_argument("rom")

    p_list = sub.add_parser("list", help="List found assets")
    p_list.add_argument("rom")
    p_list.add_argument("--query", "-q", default="", help="Filter by path/kind/magic")
    p_list.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve Nitro files inside unknown containers")

    p_export = sub.add_parser("export", help="Export found assets")
    p_export.add_argument("rom")
    p_export.add_argument("--out", "-o", required=True)
    p_export.add_argument("--query", "-q", default="", help="Filter by path/kind/magic")
    p_export.add_argument("--original", action="store_true", help="Export original compressed data instead of decoded data")
    p_export.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve Nitro files inside unknown containers")

    p_convert = sub.add_parser("convert", help="Convert matching model assets with apicula")
    p_convert.add_argument("rom")
    p_convert.add_argument("--out", "-o", required=True)
    p_convert.add_argument("--query", "-q", default="BMD0", help="Filter assets before conversion")
    p_convert.add_argument("--limit", type=int, default=0, help="Optional conversion limit")
    p_convert.add_argument("--format", choices=["glb", "dae"], default="glb", help="apicula output format")
    p_convert.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve Nitro files inside unknown containers")

    p_tex = sub.add_parser("textures", help="Extract PNG/image candidates from matching BTX0 texture assets with apicula")
    p_tex.add_argument("rom")
    p_tex.add_argument("--out", "-o", required=True)
    p_tex.add_argument("--query", "-q", default="BTX0", help="Filter texture assets before extraction")
    p_tex.add_argument("--limit", type=int, default=0, help="Optional extraction limit")
    p_tex.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve Nitro files inside unknown containers")

    p_decode = sub.add_parser("decode", help="Decode matching readable assets to PNG when NDS-AS supports the format")
    p_decode.add_argument("rom")
    p_decode.add_argument("--out", "-o", required=True)
    p_decode.add_argument("--query", "-q", default="BTX0", help="Filter assets before PNG decode; try BTX0, RGCN, RLCN, RCSN, PNG")
    p_decode.add_argument("--limit", type=int, default=0, help="Optional decode limit")
    p_decode.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve known files inside unknown containers")

    p_audio = sub.add_parser("audio", help="Export lossless SDAT/SSEQ/SSAR/SBNK/SWAR/SWAV/STRM audio bundles; WAV where NDS-AS can decode samples/streams")
    p_audio.add_argument("rom")
    p_audio.add_argument("--out", "-o", default="exports/audio", help="Output folder")
    p_audio.add_argument("--query", "-q", default="", help="Optional filter before audio export; try SDAT, SWAR, SWAV, STRM, SSEQ, SSAR, or SBNK")
    p_audio.add_argument("--limit", type=int, default=0, help="Optional export limit")
    p_audio.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve known files inside unknown containers")

    args = parser.parse_args(argv)

    if args.cmd == "install":
        from .install import install
        return install(with_apicula=not args.no_apicula)

    if args.cmd == "mappings":
        from .mapping import load_mappings
        for m in load_mappings():
            preset_count = len(getattr(m, "search_presets", []))
            print(f"{m.mapping_id:<16} {m.label:<32} {m.coverage}  presets:{preset_count}")
            for preset in getattr(m, "search_presets", [])[:6]:
                print(f"  - {preset.label}: {preset.query}")
        return 0

    if args.cmd in {None, "run"}:
        from .app import main as app_main
        app_main()
        return 0

    if args.cmd == "info":
        print_rom_info(args.rom)
        return 0

    print(f"Scanning {args.rom}...")
    try:
        assets = scan_nds_path(args.rom, progress=lambda msg: print(msg, file=sys.stderr), carve_unknown_blobs=getattr(args, "deep_scan", False))
    except Exception as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1
    assets = filter_assets(assets, getattr(args, "query", ""))

    if args.cmd == "list":
        print_assets(assets)
        print(f"\nFound {len(assets)} matching asset(s).")
        return 0

    if args.cmd == "export":
        exported = export_assets(assets, args.out, decoded=not args.original)
        print(f"Exported {len(exported)} asset(s) to {args.out}")
        return 0

    if args.cmd == "audio":
        out = Path(args.out)
        selected = [a for a in assets if a.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}]
        if args.limit > 0:
            selected = selected[:args.limit]
        total = 0
        for asset in selected:
            try:
                written = export_readable_asset(asset, out)
            except Exception as exc:
                print(f"ERR {asset.virtual_path}: {exc}", file=sys.stderr)
                continue
            total += len(written)
            print(f"OK  {asset.virtual_path}")
            for f in written[:20]:
                print(f"    -> {f}")
        print(f"Exported {total} audio-related file(s) to {out}")
        return 0

    if args.cmd == "decode":
        out = Path(args.out)
        selected = assets
        if args.limit > 0:
            selected = selected[:args.limit]
        total = 0
        for asset in selected:
            try:
                written = export_readable_asset(asset, out)
            except Exception as exc:
                print(f"ERR {asset.virtual_path}: {exc}", file=sys.stderr)
                continue
            if written:
                total += len(written)
                print(f"OK  {asset.virtual_path}")
                for f in written[:12]:
                    print(f"    -> {f}")
        print(f"Decoded {total} readable PNG file(s) to {out}")
        return 0

    if args.cmd == "textures":
        out = Path(args.out)
        textures = [a for a in assets if a.magic == "BTX0"]
        if args.limit > 0:
            textures = textures[:args.limit]
        extracted = 0
        for asset in textures:
            tex_out = out / asset.asset_id
            result = convert_texture_with_apicula(asset, tex_out)
            if result.ok:
                images = texture_outputs(tex_out)
                extracted += len(images) or len(result.output_files)
                print(f"OK  {asset.virtual_path}")
                for f in result.output_files[:12]:
                    print(f"    -> {f}")
            else:
                print(f"ERR {asset.virtual_path}: {result.message}", file=sys.stderr)
        print(f"Extracted {extracted} texture/image file(s) to {out}")
        return 0

    if args.cmd == "convert":
        out = Path(args.out)
        models = [a for a in assets if a.magic == "BMD0"]
        if args.limit > 0:
            models = models[:args.limit]
        converted = 0
        for asset in models:
            siblings = sibling_assets(asset, assets)
            # Use a stable subfolder per model so batch conversion does not clobber
            # earlier converted files when apicula runs with --overwrite.
            model_out = out / asset.asset_id
            result = convert_with_apicula(asset, model_out, sibling_assets=siblings, output_format=args.format)
            if result.ok:
                converted += len(result.output_files)
                print(f"OK  {asset.virtual_path}")
                for f in result.output_files:
                    print(f"    -> {f}")
            else:
                print(f"ERR {asset.virtual_path}: {result.message}", file=sys.stderr)
        print(f"Converted {converted} output file(s) to {out}")
        return 0

    parser.print_help()
    return 2


def print_rom_info(rom_path: str) -> None:
    try:
        rom = NDSRom.from_path(rom_path)
        files = list(rom.iter_files())
    except Exception as exc:
        print(f"Could not read ROM: {exc}", file=sys.stderr)
        raise SystemExit(1)
    profile = detect_profile(rom.info.title, rom.info.game_code, [f.path for f in files])
    print(f"Title:      {rom.info.title or '(unknown)'}")
    print(f"Game code:  {rom.info.game_code or '(unknown)'}")
    print(f"Maker code: {rom.info.maker_code or '(unknown)'}")
    print(f"Files:      {len(files)}")
    print(f"Profile:    {profile.label} ({profile.confidence})")
    mapping = choose_mapping(rom.info.title, rom.info.game_code)
    print(mapping_summary(mapping))
    if profile.priority_paths:
        print("\nPriority paths / filters:")
        for path in profile.priority_paths:
            print(f"  - {path}")
    if profile.priority_queries:
        print("\nUseful searches:")
        print("  " + ", ".join(profile.priority_queries))
    if profile.notes:
        print("\nNotes:")
        for note in profile.notes:
            print(f"  - {note}")


def print_assets(assets: list[Asset]) -> None:
    for i, asset in enumerate(assets, start=1):
        flags: list[str] = []
        if asset.compressed:
            flags.append("compressed")
        if asset.carved:
            flags.append(f"carved@0x{asset.carved_offset:X}" if asset.carved_offset is not None else "carved")
        marker = f" [{' '.join(flags)}]" if flags else ""
        cat = getattr(asset, "mapping_category", "unknown") or "unknown"
        print(f"{i:04d}  {asset.magic:<4}  {cat:<18}  {asset.kind:<24}  {human_size(asset.size):>10}  {asset.virtual_path}{marker}")


def sibling_assets(asset: Asset, assets: list[Asset]) -> list[Asset]:
    useful_magics = {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}
    model_names = extract_nitro_names(asset.data)
    scored: list[tuple[int, str, Asset]] = []
    for candidate in assets:
        if candidate.asset_id == asset.asset_id or candidate.magic not in useful_magics:
            continue
        score = 100
        if candidate.magic == "BTX0" and model_names:
            overlap = model_names & extract_nitro_names(candidate.data)
            if overlap:
                score = max(0, 10 - min(len(overlap), 10))
        if score == 100 and candidate.folder_key == asset.folder_key:
            score = 15
        if score == 100 and asset.container_chain and candidate.container_chain and asset.container_chain[-1:] == candidate.container_chain[-1:]:
            score = 20
        path = asset.virtual_path.casefold()
        cpath = candidate.virtual_path.casefold()
        if score == 100 and "a/0/0/8" in path and "a/0/1/4" in cpath:
            score = 35
        if score < 100:
            scored.append((score, candidate.virtual_path, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _score, _path, candidate in scored[:64]]


if __name__ == "__main__":
    raise SystemExit(main())
