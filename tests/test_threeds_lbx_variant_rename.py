from pathlib import Path
import zipfile

from rae.platforms.threeds.lbx_catalog import lbx_variant_letter


def _migration_module():
    from scripts.rename_lbx_numbered_variants import apply_change, renamed_title

    return apply_change, renamed_title


def test_numbered_variant_title_moves_letter_before_primary_number() -> None:
    _apply_change, renamed_title = _migration_module()

    assert renamed_title("Battery 02 1") == "Battery A 02"
    assert renamed_title("Tower Shield 05 2") == "Tower Shield B 05"
    assert renamed_title("Option 03 3 L") == "Option C 03 L"
    assert renamed_title("Battery 02") is None
    assert lbx_variant_letter(26) == "AA"

    from scripts.rename_lbx_numbered_variants import original_title

    assert original_title("Battery A 02") == "Battery 02 1"
    assert original_title("Option AA 03 L") == "Option 03 27 L"


def test_completed_package_is_renamed_inside_and_out(tmp_path: Path) -> None:
    apply_change, _renamed_title = _migration_module()
    directory = tmp_path / "Battery 02 1"
    directory.mkdir()
    for suffix in ("_icon.png", "_preview.png", "_preview.glb"):
        (directory / f"Battery 02 1{suffix}").write_bytes(suffix.encode())
    archive = directory / "Battery 02 1.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("Battery 02 1.dae", "dae")
        output.writestr("texture.png", "png")

    apply_change(directory, "Battery A 02")

    target = tmp_path / "Battery A 02"
    assert sorted(path.name for path in target.iterdir()) == [
        "Battery A 02.zip",
        "Battery A 02_icon.png",
        "Battery A 02_preview.glb",
        "Battery A 02_preview.png",
    ]
    with zipfile.ZipFile(target / "Battery A 02.zip") as result:
        assert result.namelist() == ["Battery A 02.dae", "texture.png"]
