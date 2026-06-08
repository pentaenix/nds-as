"""CLI argument parser definition."""
from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rae",
        description="RAE (Retro Asset Extractor): explore and export assets from your own ROM dumps",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("run", help="Open the desktop UI")
    p_install = sub.add_parser(
        "install",
        help="Create/refresh .venv, requirements, RAE editable install, safe folders, and optional apicula",
    )
    p_install.add_argument("--no-apicula", action="store_true", help="Skip optional apicula clone/build")

    sub.add_parser("mappings", help="List available community mapping files")

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

    p_decode = sub.add_parser("decode", help="Decode matching readable assets to PNG when RAE supports the format")
    p_decode.add_argument("rom")
    p_decode.add_argument("--out", "-o", required=True)
    p_decode.add_argument("--query", "-q", default="BTX0", help="Filter assets before PNG decode; try BTX0, RGCN, RLCN, RCSN, PNG")
    p_decode.add_argument("--limit", type=int, default=0, help="Optional decode limit")
    p_decode.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve known files inside unknown containers")

    p_audio = sub.add_parser(
        "audio",
        help="Export lossless SDAT/SSEQ/SSAR/SBNK/SWAR/SWAV/STRM audio bundles; WAV where RAE can decode samples/streams",
    )
    p_audio.add_argument("rom")
    p_audio.add_argument("--out", "-o", default="exports/audio", help="Output folder")
    p_audio.add_argument("--query", "-q", default="", help="Optional filter before audio export; try SDAT, SWAR, SWAV, STRM, SSEQ, SSAR, or SBNK")
    p_audio.add_argument("--limit", type=int, default=0, help="Optional export limit")
    p_audio.add_argument("--deep-scan", action="store_true", help="Slower fallback: carve known files inside unknown containers")

    return parser
