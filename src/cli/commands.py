"""CLI command handlers and ROM scan helpers."""
from __future__ import annotations

import sys
from pathlib import Path

from ..exporter import convert_texture_with_apicula, convert_with_apicula, export_assets, export_readable_asset, texture_outputs
from ..mapping import choose_mapping, load_mappings, mapping_summary
from ..nds import NDSRom
from ..nitro_names import extract_nitro_names
from ..profiles import detect_profile
from ..platforms import platform_for_path
from ..scanner import Asset, filter_assets, scan_nds_path
from ..util import human_size


def dispatch(cmd: str | None, args) -> int:
    if cmd == "install":
        from ..install import install

        return install(with_apicula=not args.no_apicula)

    if cmd == "mappings":
        return cmd_mappings()

    if cmd in {None, "run"}:
        from ..app import main as app_main

        app_main()
        return 0

    if cmd == "info":
        print_rom_info(args.rom)
        return 0

    if cmd == "home":
        from ..platforms.home.cli import dispatch_home

        return dispatch_home(args)

    print(f"Scanning {args.rom}...", file=sys.stderr)
    try:
        platform = platform_for_path(Path(args.rom))
        if platform is not None and platform.id != "nds" and platform.scan_rom_path is not None:
            assets = platform.scan_rom_path(args.rom, progress=lambda msg: print(msg, file=sys.stderr))
        else:
            assets = scan_nds_path(
                args.rom,
                progress=lambda msg: print(msg, file=sys.stderr),
                carve_unknown_blobs=getattr(args, "deep_scan", False),
            )
    except Exception as exc:
        print(f"Scan failed: {exc}", file=sys.stderr)
        return 1
    assets = filter_assets(assets, getattr(args, "query", ""))

    if cmd == "list":
        print_assets(assets)
        print(f"\nFound {len(assets)} matching asset(s).")
        return 0

    if cmd == "export":
        exported = export_assets(assets, args.out, decoded=not args.original)
        print(f"Exported {len(exported)} asset(s) to {args.out}")
        return 0

    if cmd == "audio":
        return cmd_audio(assets, args)

    if cmd == "decode":
        return cmd_decode(assets, args)

    if cmd == "textures":
        return cmd_textures(assets, args)

    if cmd == "convert":
        return cmd_convert(assets, args)

    return 2


def cmd_mappings() -> int:
    for mapping in load_mappings():
        preset_count = len(getattr(mapping, "search_presets", []))
        print(f"{mapping.platform:<4} {mapping.mapping_id:<16} {mapping.label:<32} {mapping.coverage}  presets:{preset_count}")
        for preset in getattr(mapping, "search_presets", [])[:6]:
            print(f"  - {preset.label}: {preset.query}")
    return 0


def cmd_audio(assets: list[Asset], args) -> int:
    out = Path(args.out)
    selected = [a for a in assets if a.magic in {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}]
    if args.limit > 0:
        selected = selected[: args.limit]
    total = 0
    for asset in selected:
        try:
            written = export_readable_asset(asset, out)
        except Exception as exc:
            print(f"ERR {asset.virtual_path}: {exc}", file=sys.stderr)
            continue
        total += len(written)
        print(f"OK  {asset.virtual_path}")
        for path in written[:20]:
            print(f"    -> {path}")
    print(f"Exported {total} audio-related file(s) to {out}")
    return 0


def cmd_decode(assets: list[Asset], args) -> int:
    out = Path(args.out)
    selected = assets[: args.limit] if args.limit > 0 else assets
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
            for path in written[:12]:
                print(f"    -> {path}")
    print(f"Decoded {total} readable PNG file(s) to {out}")
    return 0


def cmd_textures(assets: list[Asset], args) -> int:
    out = Path(args.out)
    textures = [a for a in assets if a.magic == "BTX0"]
    if args.limit > 0:
        textures = textures[: args.limit]
    extracted = 0
    for asset in textures:
        tex_out = out / asset.asset_id
        result = convert_texture_with_apicula(asset, tex_out)
        if result.ok:
            images = texture_outputs(tex_out)
            extracted += len(images) or len(result.output_files)
            print(f"OK  {asset.virtual_path}")
            for path in result.output_files[:12]:
                print(f"    -> {path}")
        else:
            print(f"ERR {asset.virtual_path}: {result.message}", file=sys.stderr)
    print(f"Extracted {extracted} texture/image file(s) to {out}")
    return 0


def cmd_convert(assets: list[Asset], args) -> int:
    out = Path(args.out)
    models = [a for a in assets if a.magic == "BMD0"]
    if args.limit > 0:
        models = models[: args.limit]
    converted = 0
    for asset in models:
        siblings = sibling_assets(asset, assets)
        model_out = out / asset.asset_id
        result = convert_with_apicula(asset, model_out, sibling_assets=siblings, output_format=args.format)
        if result.ok:
            converted += len(result.output_files)
            print(f"OK  {asset.virtual_path}")
            for path in result.output_files:
                print(f"    -> {path}")
        else:
            print(f"ERR {asset.virtual_path}: {result.message}", file=sys.stderr)
    print(f"Converted {converted} output file(s) to {out}")
    return 0


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
    for index, asset in enumerate(assets, start=1):
        flags: list[str] = []
        if asset.compressed:
            flags.append("compressed")
        if asset.carved:
            flags.append(f"carved@0x{asset.carved_offset:X}" if asset.carved_offset is not None else "carved")
        marker = f" [{' '.join(flags)}]" if flags else ""
        category = getattr(asset, "mapping_category", "unknown") or "unknown"
        print(f"{index:04d}  {asset.magic:<4}  {category:<18}  {asset.kind:<24}  {human_size(asset.size):>10}  {asset.virtual_path}{marker}")


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
        if score == 100 and asset.container_chain and candidate.container_chain and candidate.container_chain[-1:] == candidate.container_chain[-1:]:
            score = 20
        path = asset.virtual_path.casefold()
        cpath = candidate.virtual_path.casefold()
        if score == 100 and "a/0/0/8" in path and "a/0/1/4" in cpath:
            score = 35
        if score < 100:
            scored.append((score, candidate.virtual_path, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [candidate for _score, _path, candidate in scored[:64]]
