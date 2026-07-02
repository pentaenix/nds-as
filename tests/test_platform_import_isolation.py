"""Enforce per-platform import boundaries (see core/modules/platform_boundaries.py)."""
from __future__ import annotations

from pathlib import Path

from rae.core.modules.platform_boundaries import collect_import_violations
from rae.install import project_root


def test_no_forbidden_cross_platform_imports():
    src_root = project_root() / "src"
    violations = collect_import_violations(src_root=src_root)
    if violations:
        joined = "\n".join(f"  - {line}" for line in violations)
        raise AssertionError(
            "Cross-platform imports detected. Each platform must stay in its island.\n"
            f"{joined}\n\n"
            "Fix: move shared code to core/ or glb_policy/, or duplicate the helper "
            "under platforms/<your-platform>/."
        )


def test_platform_boundaries_module_lists_active_builders():
    from rae.core.modules.platform_boundaries import PLATFORM_MODULE_BUILDERS

    assert "nds" in PLATFORM_MODULE_BUILDERS
    assert "mobile" in PLATFORM_MODULE_BUILDERS
    assert "gba" not in PLATFORM_MODULE_BUILDERS
