"""CLI package for RAE."""
from __future__ import annotations

from .commands import dispatch
from .parser import build_parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    code = dispatch(args.cmd, args)
    if code == 2:
        parser.print_help()
    return code


__all__ = ["build_parser", "dispatch", "main"]
