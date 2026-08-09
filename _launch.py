#!/usr/bin/env python3
"""RAE launcher — bootstraps flat src/ layout then runs the CLI."""

from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from _rae_import import install

install()

from rae.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
