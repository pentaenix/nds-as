from pathlib import Path


def test_gitignore_allows_shared_easyfind_files():
    root = Path(__file__).resolve().parents[1]
    gitignore = (root / ".gitignore").read_text(encoding="utf-8")
    assert "easyfind/*.tmp" in gitignore
    assert "easyfind/*.easyfind.tmp" in gitignore
    assert "easyfind/*\n" not in gitignore
    assert "!easyfind/README.md" not in gitignore


def test_easyfind_folder_metadata_exists():
    root = Path(__file__).resolve().parents[1]
    easyfind_dir = root / "easyfind"
    assert easyfind_dir.is_dir()
    assert (easyfind_dir / "README.md").is_file()
    assert (easyfind_dir / ".gitkeep").is_file()
    readme = (easyfind_dir / "README.md").read_text(encoding="utf-8")
    assert "game code" in readme.lower()
    assert "validate" in readme.lower()
