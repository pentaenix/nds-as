from pathlib import Path

from rae.easyfind.paths import (
    easyfind_path_for_game_code,
    is_valid_game_code,
    normalize_game_code,
)


def test_normalize_game_code():
    assert normalize_game_code("irbo") == "IRBO"
    assert normalize_game_code(" IRBO ") == "IRBO"
    assert normalize_game_code("ipke-extra") == "IPKE"


def test_is_valid_game_code():
    assert is_valid_game_code("IRBO") is True
    assert is_valid_game_code("ir") is False
    assert is_valid_game_code("") is False


def test_easyfind_path_for_game_code():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        path = easyfind_path_for_game_code("irbo", root=root)
        assert path == root / "easyfind" / "IRBO.easyfind"
        assert path.parent.is_dir()
