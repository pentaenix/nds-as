"""Enforce platform isolation contract (see docs/agents/platform-isolation-contract.md)."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from rae.core.modules.platform_boundaries import collect_import_violations, iter_python_files
from rae.install import project_root

pytestmark = pytest.mark.core_shared


def _normalize_path(path: Path, *, src_root: Path) -> str:
    try:
        return path.resolve().relative_to(src_root.resolve()).as_posix()
    except ValueError:
        return ""


def _module_path_from_import(module_name: str) -> str | None:
    if not module_name.startswith("rae."):
        return None
    return module_name[len("rae.") :].replace(".", "/")


_PLATFORM_ID_RE = re.compile(r"^platforms/([a-z][a-z0-9_]*)")


def _platform_submodule(import_path: str) -> str | None:
    match = _PLATFORM_ID_RE.match(import_path)
    if not match:
        return None
    platform_id = match.group(1)
    if platform_id.startswith("_") or platform_id == "stub":
        return None
    return platform_id


def collect_platform_to_ui_import_violations(*, src_root: Path) -> list[str]:
    violations: list[str] = []
    for path in iter_python_files(src_root):
        rel_file = _normalize_path(path, src_root=src_root)
        if not rel_file.startswith("platforms/"):
            continue
        parts = rel_file.split("/")
        if len(parts) >= 2 and parts[1].startswith("_"):
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
                modules = [node.module] if node.module else []
            else:
                continue
            for module_name in modules:
                import_path = _module_path_from_import(module_name)
                if import_path and import_path.startswith("ui/"):
                    violations.append(f"{rel_file}: imports {module_name} — platforms must not import ui/")
    return violations


# Baseline: legacy UI → platform imports. Do not add entries without migration plan.
KNOWN_UI_PLATFORM_IMPORT_DEBT: dict[str, frozenset[str]] = {
    "ui/workers/preview.py": frozenset(
        {
            "platforms.nds.nitro.types",
            "platforms.nds.gltf.preview_textures",
        }
    ),
    "ui/preview/texture_clips.py": frozenset({"platforms.nds.gltf.texture_patch"}),
    "ui/preview/glb_preview.py": frozenset({"platforms.nds.gltf.preview_textures"}),
    "ui/preview/preview_gl.py": frozenset({"platforms.nds.gltf.preview_textures"}),
    "ui/preview/textured_mesh_item.py": frozenset({"platforms.nds.gltf.preview_textures"}),
    "ui/preview_quality.py": frozenset({"platforms.nds.gltf.preview_textures"}),
    "ui/main/texture_assigner_panel.py": frozenset({"platforms.nds.texture_assigner"}),
    "ui/main/texture_animation_panel.py": frozenset({"platforms.nds.texture_assigner"}),
    "ui/mobile_model_preview.py": frozenset(
        {
            "platforms.mobile.model_module",
            "platforms.mobile.model_module.preview",
            "platforms.mobile.model_module.viewport",
        }
    ),
    "ui/mobile_device_toolkit.py": frozenset(
        {
            "platforms.mobile.archive",
            "platforms.mobile.rom",
        }
    ),
}


def collect_ui_to_platform_import_debt(*, src_root: Path) -> dict[str, set[str]]:
    """Return ui file → set of platforms.<id>.* module paths (excluding package root)."""
    debt: dict[str, set[str]] = {}
    ui_root = src_root / "ui"
    if not ui_root.is_dir():
        return debt
    for path in iter_python_files(ui_root):
        rel_file = _normalize_path(path, src_root=src_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module] if node.module else []
            else:
                continue
            for module_name in modules:
                if not module_name or not module_name.startswith("rae.platforms."):
                    continue
                tail = module_name[len("rae.") :]
                if tail == "platforms":
                    continue
                if _platform_submodule(tail.replace(".", "/")):
                    imports.add(tail)
        if imports:
            debt[rel_file] = imports
    return debt


def test_no_forbidden_cross_platform_imports():
    src_root = project_root() / "src"
    violations = collect_import_violations(src_root=src_root)
    if violations:
        joined = "\n".join(f"  - {line}" for line in violations)
        raise AssertionError(
            "Cross-platform imports detected.\n"
            f"{joined}\n\n"
            "See docs/agents/platform-isolation-contract.md"
        )


def test_platforms_never_import_ui():
    src_root = project_root() / "src"
    violations = collect_platform_to_ui_import_violations(src_root=src_root)
    if violations:
        joined = "\n".join(f"  - {line}" for line in violations)
        raise AssertionError(f"Platform → UI imports forbidden.\n{joined}")


def test_no_platforms_shared_package():
    shared = project_root() / "src" / "platforms" / "shared"
    assert not shared.exists(), (
        "platforms/shared/ is forbidden. Duplicate helpers per island instead. "
        "See docs/agents/platform-isolation-contract.md"
    )


def test_no_top_level_glb_policy_package():
    glb_policy = project_root() / "src" / "glb_policy"
    assert not glb_policy.exists(), (
        "src/glb_policy/ is forbidden. Each platform owns platforms/<id>/gltf/. "
        "See docs/agents/platform-isolation-contract.md"
    )


def test_no_imports_from_rae_glb_policy():
    src_root = project_root() / "src"
    forbidden: list[str] = []
    for path in iter_python_files(src_root):
        rel = _normalize_path(path, src_root=src_root)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module] if node.module else []
            else:
                continue
            for module_name in modules:
                if module_name and (
                    module_name == "rae.glb_policy"
                    or module_name.startswith("rae.glb_policy.")
                ):
                    forbidden.append(f"{rel}: imports {module_name}")
    if forbidden:
        joined = "\n".join(f"  - {line}" for line in sorted(forbidden))
        raise AssertionError(
            "rae.glb_policy is removed — use platforms/<id>/gltf/:\n" + joined
        )


def test_platforms_do_not_import_shared_glb_policy():
    src_root = project_root() / "src"
    platforms_root = src_root / "platforms"
    forbidden = []
    for platform_dir in sorted(platforms_root.iterdir()):
        if not platform_dir.is_dir() or platform_dir.name.startswith("_"):
            continue
        for path in iter_python_files(platform_dir):
            rel = _normalize_path(path, src_root=src_root)
            if "/gltf/" in rel:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module] if node.module else []
                else:
                    continue
                for module_name in modules:
                    if module_name and (
                        module_name.startswith("rae.glb_policy")
                        or (
                            module_name.endswith("glb_policy")
                            and not module_name.endswith("apply_platform_glb_policy")
                        )
                    ):
                        forbidden.append(f"{rel}: imports {module_name}")
    if forbidden:
        joined = "\n".join(f"  - {line}" for line in forbidden)
        raise AssertionError(
            "Platforms must use local gltf/, not shared rae.glb_policy:\n" + joined
        )


def test_ui_platform_import_debt_not_grown():
    src_root = project_root() / "src"
    current = collect_ui_to_platform_import_debt(src_root=src_root)
    for rel_file, modules in current.items():
        allowed = KNOWN_UI_PLATFORM_IMPORT_DEBT.get(rel_file, frozenset())
        extra = modules - set(allowed)
        if extra:
            raise AssertionError(
                f"{rel_file}: new platform import(s) {sorted(extra)}. "
                "UI must use PlatformDispatch. Update KNOWN_UI_PLATFORM_IMPORT_DEBT "
                "only when migrating debt, not when adding features. "
                "See docs/agents/platform-isolation-contract.md"
            )


def test_scaffold_template_includes_isolation_stubs():
    root = project_root() / "src" / "platforms" / "_template"
    required = [
        root / "glb_policy.py",
        root / "preview" / "material-policy.js",
        root / "preview" / "README.md",
        root / "inspector.py",
    ]
    missing = [str(p.relative_to(project_root())) for p in required if not p.is_file()]
    assert not missing, f"_template missing isolation stubs: {missing}"
