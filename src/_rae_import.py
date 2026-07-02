"""Map import rae.* to the flat src/ tree (no src/rae/ nesting)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent


def install() -> None:
    if any(getattr(hook, "__rae_flat_src__", False) for hook in sys.meta_path):
        return

    class _RaeFlatSrcFinder:
        __rae_flat_src__ = True

        def find_spec(self, fullname, path, target=None):
            if fullname == "rae":
                rel = ""
            elif fullname.startswith("rae."):
                rel = fullname[len("rae.") :].replace(".", "/")
            else:
                return None
            if rel:
                candidate = _SRC / rel
                if candidate.is_dir():
                    candidate = candidate / "__init__.py"
                elif not candidate.with_suffix(".py").is_file():
                    candidate = candidate / "__init__.py"
                else:
                    candidate = candidate.with_suffix(".py")
            else:
                candidate = _SRC / "__init__.py"
            if not candidate.is_file():
                return None
            return importlib.util.spec_from_file_location(fullname, candidate)

    sys.meta_path.insert(0, _RaeFlatSrcFinder())
