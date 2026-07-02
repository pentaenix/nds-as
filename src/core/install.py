from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


def project_root() -> Path:
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").exists() and (parent / "src" / "rae").exists():
            return parent
    return Path.cwd()


def venv_python(root: Path) -> Path:
    if os.name == "nt":
        return root / ".venv" / "Scripts" / "python.exe"
    return root / ".venv" / "bin" / "python"


def run(cmd: list[str], *, cwd: Path) -> int:
    print("$ " + " ".join(cmd))
    return subprocess.call(cmd, cwd=str(cwd))


def ensure_dirs(root: Path) -> None:
    for name, readme in {
        "roms": "Put your own legally dumped Nintendo DS ROMs here. Do not commit ROM files.\n",
        "exports": "RAE writes extracted/converted assets here. Do not commit exported assets.\n",
        "saves": "RAE session files live here. Sessions can contain extracted asset data and must stay local.\n",
        "easyfind": (
            "EasyFind index files keyed by Nintendo DS game code (for example IRBO.easyfind). "
            "Built indexes are local artifacts and stay gitignored.\n"
        ),
        "texture_index": (
            "Texture dictionary index files keyed by Nintendo DS game code (for example IRBO.texture-index). "
            "Built indexes are local artifacts and stay gitignored.\n"
        ),
        "tools": "Optional external tools such as apicula can live here.\n",
    }.items():
        path = root / name
        path.mkdir(exist_ok=True)
        (path / ".gitkeep").touch(exist_ok=True)
        readme_path = path / "README.md"
        if not readme_path.exists():
            readme_path.write_text(f"# {name}/\n\n{readme}", encoding="utf-8")


def ensure_gitignore(root: Path) -> None:
    required = [
        "roms/**", "!roms/README.md", "!roms/.gitkeep", "",
        "exports/**", "!exports/README.md", "!exports/.gitkeep", "",
        "saves/*", "!saves/README.md", "!saves/.gitkeep", "",
        "easyfind/**", "!easyfind/README.md", "!easyfind/.gitkeep", "",
        "easyfind/*.tmp", "easyfind/*.easyfind.tmp", "",
        "texture_index/**", "!texture_index/README.md", "!texture_index/.gitkeep", "",
        ".cache/**", "",
        ".venv/", "__pycache__/", "*.pyc", ".DS_Store",
    ]
    path = root / ".gitignore"
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = current.splitlines()
    changed = False
    for item in required:
        if item and item not in lines:
            lines.append(item)
            changed = True
        elif item == "" and (not lines or lines[-1] != ""):
            lines.append("")
    if changed or not path.exists():
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def install(*, with_apicula: bool = True) -> int:
    root = project_root()
    print(f"RAE install/refresh in {root}")
    ensure_dirs(root)
    ensure_gitignore(root)

    py = venv_python(root)
    if not py.exists():
        print("Creating .venv ...")
        rc = run([sys.executable, "-m", "venv", ".venv"], cwd=root)
        if rc:
            return rc
    else:
        print(f"Using existing virtualenv: {py}")

    rc = run([str(py), "-m", "pip", "install", "--upgrade", "pip"], cwd=root)
    if rc:
        return rc
    if (root / "requirements.txt").exists():
        rc = run([str(py), "-m", "pip", "install", "-r", "requirements.txt"], cwd=root)
        if rc:
            return rc
    rc = run([str(py), "-m", "pip", "install", "-e", "."], cwd=root)
    if rc:
        return rc

    if with_apicula:
        ensure_apicula(root)

    print("\nRAE health check")
    print(f"Python:   OK {platform.python_version()} -> {py}")
    print("venv:     OK .venv")
    print("RAE:   OK editable install")
    for folder in ("roms", "exports", "saves", "easyfind", "texture_index", "mappings"):
        print(f"{folder + ':':<10} OK {(root / folder).exists()}")
    apicula = find_apicula(root)
    if apicula:
        print(f"apicula:  OK {apicula}")
    else:
        print("apicula:  optional, not found/built. Model conversion still needs it.")
    print("\nReady:")
    print("  ./rae run")
    print("  ./rae list roms/your_game.nds")
    print("  ./rae decode roms/your_game.nds --out exports/readable")
    return 0


def find_apicula(root: Path) -> Path | None:
    names = ["apicula.exe", "apicula"] if os.name == "nt" else ["apicula"]
    env = (
        os.environ.get("RAE_APICULA")
        or os.environ.get("DSAS_APICULA")
        or os.environ.get("DSM_APICULA")
    )
    candidates = []
    if env:
        candidates.append(Path(env).expanduser())
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
        candidates.append(root / "tools" / "apicula" / "target" / "release" / name)
    for c in candidates:
        if c.exists() and c.is_file():
            return c
    return None


def ensure_apicula(root: Path) -> None:
    if find_apicula(root):
        print("apicula already available.")
        return
    git = shutil.which("git")
    cargo = shutil.which("cargo")
    if not git or not cargo:
        print("\napicula not built. This is optional, but model conversion/preview needs it.")
        if not git:
            print("Missing git.")
        if not cargo:
            print("Missing Rust/Cargo.")
            print("Install Rust from https://rustup.rs or your package manager, then rerun ./rae install")
        return
    tools = root / "tools"
    tools.mkdir(exist_ok=True)
    apicula_dir = tools / "apicula"
    if not apicula_dir.exists():
        print("Cloning apicula ...")
        rc = run([git, "clone", "https://github.com/scurest/apicula.git", str(apicula_dir)], cwd=root)
        if rc:
            print("Could not clone apicula; RAE can still run without model conversion.")
            return
    print("Building apicula ...")
    rc = run([cargo, "build", "--release"], cwd=apicula_dir)
    if rc:
        print("Could not build apicula; RAE can still run without model conversion.")


def main(argv: list[str] | None = None) -> int:
    return install()


if __name__ == "__main__":
    raise SystemExit(main())
