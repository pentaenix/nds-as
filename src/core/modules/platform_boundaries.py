"""Import-boundary rules for per-platform isolation.

Each ROM platform is an *island*: scan, decode, preview, export, and format hacks live
under ``platforms/<id>/``.  Shared code is limited to contracts (this package), the
ROM registry, and format-neutral GLB helpers (``glb_policy/``).

Duplicating helpers per platform is preferred over cross-platform imports.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Platform module builders (lazy import — no cross-platform loading at startup)
# ---------------------------------------------------------------------------

PLATFORM_MODULE_BUILDERS: dict[str, str] = {
    "nds": "rae.platforms.nds.platform_modules:build_nds_modules",
    "mobile": "rae.platforms.mobile.platform_modules:build_mobile_modules",
    "3ds": "rae.platforms.threeds.platform_modules:build_threeds_modules",
}

# ROM platforms that may install Device Toolkit menus when active.
TOOLKIT_PLATFORM_IDS: tuple[str, ...] = ("mobile", "nds")


@dataclass(frozen=True, slots=True)
class ImportRule:
    """If *importer* imports *importee*, that is a violation unless whitelisted."""

    importer_prefix: str
    importee_prefix: str
    reason: str


# Hard bans: platform A must never import platform B.
FORBIDDEN_CROSS_PLATFORM: tuple[ImportRule, ...] = (
    ImportRule("platforms/nds", "platforms/mobile", "NDS must not depend on mobile/HOME"),
    ImportRule("platforms/nds", "platforms/home", "NDS must not depend on HOME"),
    ImportRule("platforms/nds", "platforms/unity", "NDS must not depend on Unity export path"),
    ImportRule("platforms/gba", "platforms/nds", "GBA must not depend on NDS"),
    ImportRule("platforms/gba", "platforms/mobile", "GBA must not depend on mobile"),
    ImportRule("platforms/gba", "platforms/home", "GBA must not depend on HOME"),
    ImportRule("platforms/gba", "platforms/unity", "GBA must not depend on Unity helpers"),
    ImportRule("platforms/gb", "platforms/nds", "GB must not depend on NDS"),
    ImportRule("platforms/gbc", "platforms/nds", "GBC must not depend on NDS"),
    ImportRule("platforms/threeds", "platforms/nds", "3DS must not depend on NDS"),
    ImportRule("platforms/threeds", "platforms/mobile", "3DS must not depend on mobile"),
    ImportRule("platforms/mobile", "platforms/nds", "Mobile must not depend on NDS"),
    ImportRule("platforms/home", "platforms/nds", "HOME must not depend on NDS"),
)

# Explicit allow-list for intentional sub-layers (mobile → home/unity only).
ALLOWED_CROSS_PLATFORM: tuple[ImportRule, ...] = (
    ImportRule("platforms/mobile", "platforms/home", "HOME is an internal mobile sub-layer"),
    ImportRule("platforms/mobile", "platforms/unity", "Unity bundles are mobile infrastructure"),
    ImportRule("platforms/home", "platforms/mobile", "HOME mesh export uses mobile mesh_export"),
    ImportRule("platforms/home", "platforms/unity", "HOME legacy Unity shim re-exports"),
    ImportRule("platforms/home", "platforms/android", "HOME APK cache scanning"),
)


def _normalize_path(path: Path, *, src_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(src_root.resolve())
    except ValueError:
        return ""
    return rel.as_posix()


def _module_path_from_import(module_name: str) -> str | None:
    if not module_name.startswith("rae."):
        return None
    tail = module_name[len("rae.") :].replace(".", "/")
    return tail


def _platform_prefix(module_path: str) -> str | None:
    if not module_path.startswith("platforms/"):
        return None
    parts = module_path.split("/")
    if len(parts) < 2:
        return None
    return f"platforms/{parts[1]}"


def _is_allowed(importer: str, importee: str) -> bool:
    for rule in ALLOWED_CROSS_PLATFORM:
        if importer.startswith(rule.importer_prefix) and importee.startswith(rule.importee_prefix):
            return True
    return False


def _is_forbidden(importer: str, importee: str) -> ImportRule | None:
    if importer == importee:
        return None
    if _is_allowed(importer, importee):
        return None
    for rule in FORBIDDEN_CROSS_PLATFORM:
        if importer.startswith(rule.importer_prefix) and importee.startswith(rule.importee_prefix):
            return rule
    # Any other cross-platform import under platforms/ is forbidden.
    if (
        importer.startswith("platforms/")
        and importee.startswith("platforms/")
        and importer.split("/")[1] != importee.split("/")[1]
        and importee.split("/")[1] not in {"stub", "__pycache__"}
    ):
        return ImportRule(importer, importee, "Cross-platform import not on allow-list")
    return None


def iter_python_files(root: Path) -> list[Path]:
    skip = {"__pycache__", ".venv", "vendor"}
    out: list[Path] = []
    for path in root.rglob("*.py"):
        if any(part in skip for part in path.parts):
            continue
        out.append(path)
    return out


def collect_import_violations(*, src_root: Path | None = None) -> list[str]:
    """Return human-readable violation messages for all Python files under *src_root*."""
    if src_root is None:
        from ...install import project_root

        src_root = project_root() / "src"

    violations: list[str] = []
    for path in iter_python_files(src_root):
        rel_file = _normalize_path(path, src_root=src_root)
        if not rel_file.startswith("platforms/"):
            continue
        parts = rel_file.split("/")
        if len(parts) >= 2 and parts[1].startswith("_"):
            continue
        importer_platform = _platform_prefix(rel_file)
        if importer_platform is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            violations.append(f"{rel_file}: syntax error — {exc}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    modules = [node.module]
                else:
                    continue
            else:
                continue
            for module_name in modules:
                importee_path = _module_path_from_import(module_name)
                if importee_path is None:
                    continue
                importee_platform = _platform_prefix(importee_path)
                if importee_platform is None:
                    continue
                rule = _is_forbidden(importer_platform, importee_platform)
                if rule is not None:
                    violations.append(
                        f"{rel_file}: imports {module_name} — {rule.reason}"
                    )
    return violations


def load_platform_modules_builder(platform_id: str):
    """Import and return ``build_*_modules`` for *platform_id*."""
    import importlib

    target = PLATFORM_MODULE_BUILDERS.get(platform_id)
    if target is None:
        return None
    module_name, attr = target.split(":")
    module = importlib.import_module(module_name)
    return getattr(module, attr)
