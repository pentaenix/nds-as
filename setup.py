"""Setuptools entry for flat src/ layout (import rae → src/)."""

from setuptools import find_packages, setup

setup(
    package_dir={"rae": "src", "dsm": "src/dsm"},
    packages=(
        ["rae"]
        + [f"rae.{name}" for name in find_packages("src", exclude=["dsm*"])]
        + find_packages("src/dsm")
    ),
)
