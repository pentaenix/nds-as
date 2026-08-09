"""Known Windows disc profiles.

Profiles are intentionally identified from the ISO directory rather than a
filename alone.  A filename hint is used only for useful locked-tool messaging.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .container import IsoEntry


@dataclass(frozen=True, slots=True)
class WindowsIsoProfile:
    profile_id: str
    title: str
    year: int
    required_iso_members: tuple[str, ...]
    installer_members: tuple[str, ...]


MARINE_PARK_EMPIRE_2005 = WindowsIsoProfile(
    profile_id="marine_park_empire_2005",
    title="Marine Park Empire",
    year=2005,
    required_iso_members=(
        "marineparkempire.exe",
        "mpe.exe",
        "data1.hdr",
        "data1.cab",
        "data2.cab",
    ),
    installer_members=("data1.hdr", "data1.cab", "data2.cab"),
)

KNOWN_PROFILES = (MARINE_PARK_EMPIRE_2005,)


def identify_profile(entries: Iterable[IsoEntry]) -> WindowsIsoProfile | None:
    members = {entry.path.casefold() for entry in entries if not entry.is_dir}
    for profile in KNOWN_PROFILES:
        if set(profile.required_iso_members).issubset(members):
            return profile
    return None


def profile_hint_from_filename(source: Path) -> WindowsIsoProfile | None:
    normalized = "".join(char for char in source.stem.casefold() if char.isalnum())
    if "marineparkempire" in normalized:
        return MARINE_PARK_EMPIRE_2005
    return None
