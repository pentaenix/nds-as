"""LBX-specific names and Models Resource export planning.

The retail ROM keeps readable model keys in RomFS, while the localized names
live in BTX string tables and ``LBXData.pac``.  This module joins those sources
without changing the generic named-RomFS scanner used by other 3DS games.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
import re
import struct

from .container import RomFsFile, ThreedsImage


_PART_ROOTS = ("/3ddata/parts/model/", "/3ddata/parts/custom_r/")
_MODEL_ROOTS = ("/3ddata/coreparts/", *_PART_ROOTS, "/3ddata/wpn/")
_PART_SUFFIXES = (
    ("_arm_L", "Left Arm", "Arm"),
    ("_arm_R", "Right Arm", "Arm"),
    ("_body", "Body", "Core"),
    ("_head", "Head", "Head"),
    ("_leg", "Legs", "Leg"),
    ("_htail", "Tail", "Head"),
    ("_tail", "Tail", "Head"),
    ("_mantle", "Mantle", "Head"),
    ("_wing", "Other", "Head"),
)

_CHIP_TYPES = {
    "battery": ("Batteries", "Battery"),
    "cpu": ("CPUs", "CPU"),
    "motor": ("Motors", "Motor"),
    "corememory": ("Core Memories", "Core Memory"),
    "option": ("Options", "Option"),
    "coreunit": ("Core Units", "Core Unit"),
}

_WEAPON_TYPES = {
    "gu": ("Guns", {
        "ha": "Handgun", "sh": "Shotgun", "su": "Submachine Gun",
        "ma": "Machine Gun", "as": "Assault Rifle",
    }),
    "ri": ("Rifles", {"am": "AM Rifle", "sn": "Sniper Rifle"}),
    "sh": ("Shields", {
        "ba": "Heater Shield", "ro": "Round Shield",
        "sp": "Special Shield", "sq": "Tower Shield",
    }),
    "sp": ("Spears", {"cl": "Club", "la": "Lance", "na": "Glaive"}),
    "sw": ("Swords", {
        "he": "Special Sword", "li": "Short Sword",
        "lo": "Long Sword", "ra": "Rapier",
    }),
    "fi": ("Hand Weapons", {"ku": "Kunai", "fi": "Claws", "cl": "Club"}),
    "cl": ("Hammers and Axes", {"cl": "Hammer", "ax": "Axe"}),
    "as": ("Automatic Guns", {
        "as": "Assault Rifle", "ma": "Machine Gun", "sh": "Automatic Shotgun",
    }),
    "bz": ("Heavy Weapons", {"gr": "Grenade Launcher", "ro": "Rocket Launcher"}),
}


@dataclass(frozen=True, slots=True)
class LbxExportJob:
    romfs_path: str
    category: tuple[str, ...]
    title: str
    custom_r_path: str | None = None
    auxiliary_paths: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()

    @property
    def relative_directory(self) -> Path:
        return Path("LBX", *self.category, self.title)

    @property
    def source_paths(self) -> tuple[str, ...]:
        custom = (self.custom_r_path,) if self.custom_r_path else ()
        return (self.romfs_path, *custom, *self.auxiliary_paths)


def _btx_strings(payload: bytes) -> list[str]:
    if payload[:4] != b"BTX " or len(payload) < 0x18:
        raise ValueError("invalid LBX BTX string table")
    node_count = struct.unpack_from("<I", payload, 0x14)[0]
    cursor = 0x18 + node_count * 8
    strings: list[str] = []
    while cursor + 1 < len(payload):
        end = cursor
        while end + 1 < len(payload) and payload[end:end + 2] != b"\0\0":
            end += 2
        if end + 1 >= len(payload):
            break
        strings.append(payload[cursor:end].decode("utf-16-le", "replace"))
        cursor = end + 2
    return strings


def _safe_title(value: str) -> str:
    clean = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    return " ".join(clean.split()).strip(". ") or "Model"


def lbx_variant_letter(index: int) -> str:
    """Return an Excel-style letter for a zero-based LBX model variant."""
    if index < 0:
        raise ValueError("LBX variant indices cannot be negative")
    value = index + 1
    letters: list[str] = []
    while value:
        value, remainder = divmod(value - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def _numbered_variant_title(
    label: str,
    number: str,
    variant: str,
    variants: set[int],
    *,
    suffix: str = "",
) -> str:
    variant_text = (
        f" {lbx_variant_letter(int(variant))}" if len(variants) > 1 else ""
    )
    suffix_text = f" {suffix}" if suffix else ""
    return _safe_title(f"{label}{variant_text} {number}{suffix_text}")


class LbxCatalog:
    """Read localized names once and plan deterministic LBX submissions."""

    def __init__(self, rom_path: str | Path):
        self.rom_path = Path(rom_path).expanduser().resolve()
        with ThreedsImage(self.rom_path) as image:
            self.entries = image.romfs_files()
            by_path = {entry.path: entry for entry in self.entries}

            def read(path: str) -> bytes:
                entry = by_path[path]
                return image.read(entry.offset, entry.size)

            self.part_names = {
                kind: _btx_strings(read(f"/btx_Us/boost/LBX{kind}Name_jp.btx"))
                for kind in ("Head", "Core", "Arm", "Leg")
            }
            self.weapon_names = _btx_strings(
                read("/btx_Us/boost/LBXWeaponName_jp.btx")
            )
            self.robot_indices = self._robot_index_map(read("/bindata_Us/LBXData.pac"))
            self.weapon_aliases = self._weapon_alias_map(read("/bindata_Us/LBXData.pac"))

    @staticmethod
    def _pac_sections(payload: bytes) -> list[tuple[int, int]]:
        count = struct.unpack_from("<I", payload, 4)[0]
        return [struct.unpack_from("<II", payload, 0x10 + index * 8) for index in range(count)]

    def _robot_index_map(self, payload: bytes) -> dict[str, tuple[int, ...]]:
        sections = self._pac_sections(payload)
        values: dict[str, set[int]] = defaultdict(set)
        for section, record_size in ((1, 76), (3, 68), (6, 68), (7, 76)):
            offset, size = sections[section]
            for position in range(size // record_size):
                record = payload[
                    offset + position * record_size:offset + (position + 1) * record_size
                ]
                key = record[:24].split(b"\0", 1)[0].decode("ascii", "replace")
                # The first field after the 24-byte model key is the localized
                # identity used by the geometry.  The value at +52 is the
                # inventory/part label and can name a different LBX that reuses
                # this mesh, which made almost every exported folder incorrect.
                name_index = struct.unpack_from("<H", record, 24)[0]
                if key and name_index != 0xFFFF:
                    values[key].add(name_index)
        return {key: tuple(sorted(indices)) for key, indices in values.items()}

    def _weapon_alias_map(self, payload: bytes) -> dict[str, tuple[str, ...]]:
        offset, size = self._pac_sections(payload)[13]
        record_size = 88
        aliases: dict[str, list[str]] = defaultdict(list)
        for position in range(size // record_size):
            record = payload[
                offset + position * record_size:offset + (position + 1) * record_size
            ]
            key = record[:24].split(b"\0", 1)[0].decode("ascii", "replace")
            name_index = struct.unpack_from("<H", record, 24)[0]
            if key and name_index < len(self.weapon_names):
                name = self.weapon_names[name_index].strip()
                if name and name not in aliases[key]:
                    aliases[key].append(name)
        return {key: tuple(names) for key, names in aliases.items()}

    def _robot_lookup(self, key: str, part_table: str) -> tuple[str, tuple[str, ...]]:
        candidates = [key]
        sepia = re.fullmatch(r"(lbx\d+)_sepia_(\d+)", key, re.IGNORECASE)
        if sepia:
            candidates.extend((f"{sepia.group(1)}_{sepia.group(2)}", sepia.group(1)))
        variant = re.fullmatch(r"(lbx\d+)_\d+", key, re.IGNORECASE)
        if variant:
            candidates.append(variant.group(1))
        for candidate in candidates:
            names = tuple(dict.fromkeys(
                self.part_names[part_table][index]
                for index in self.robot_indices.get(candidate, ())
                if index < len(self.part_names[part_table])
                and self.part_names[part_table][index]
                and not self.part_names[part_table][index].startswith("仮追加")
            ))
            if names:
                return names[0], names
        number = re.search(r"lbx(\d+)", key, re.IGNORECASE)
        return (f"LBX {number.group(1)}" if number else key.upper()), ()

    @staticmethod
    def _part_identity(stem: str) -> tuple[str, str, str, bool]:
        transparent = stem.casefold().endswith("_tr")
        if transparent:
            stem = stem[:-3]
        custom_r = stem.casefold().endswith(("_body_r", "_leg_r"))
        if custom_r:
            stem = stem[:-2]
        for suffix, label, table in _PART_SUFFIXES:
            if stem.endswith(suffix):
                return stem[:-len(suffix)], label, table, transparent
        return stem, "Other", "Head", transparent

    def _part_job(self, path: str) -> LbxExportJob:
        stem = PurePosixPath(path).stem
        key, component, table, _transparent = self._part_identity(stem)
        name, aliases = self._robot_lookup(key, table)
        modifiers: list[str] = []
        if "_sepia_" in key.casefold():
            modifiers.append("Sepia")
        if path.startswith("/3ddata/wpn/"):
            modifiers.append("Weapon")
        category = {
            "Left Arm": "Left Arm",
            "Right Arm": "Right Arm",
            "Body": "Body",
            "Head": "Head",
            "Legs": "Legs",
            "Tail": "Tails",
            "Mantle": "Mantles",
        }.get(component, "Others")
        title = _safe_title(" ".join((name, component, *modifiers)))
        return LbxExportJob(path, ("Parts", category), title, aliases=aliases)

    def _chip_job(self, path: str, siblings: dict[str, set[int]]) -> LbxExportJob | None:
        stem = PurePosixPath(path).stem
        core_unit = re.fullmatch(r"itm_cop_coreunit(\d{2})_([SML])", stem, re.I)
        if core_unit:
            number, size = core_unit.groups()
            return LbxExportJob(
                path,
                ("Chips", "Core Units"),
                _safe_title(f"Core Unit {number} {size.upper()}"),
            )
        match = re.fullmatch(r"itm_cop_([a-z]+)(\d{2})_(\d{2})(?:_([SML]))?", stem, re.I)
        if not match:
            return None
        kind, number, variant, size = match.groups()
        config = _CHIP_TYPES.get(kind.casefold())
        if config is None:
            return None
        folder, label = config
        family = f"itm_cop_{kind}{number}"
        variants = siblings.get(family, set())
        title = _numbered_variant_title(
            label,
            number,
            variant,
            variants,
            suffix=size.upper() if size else "",
        )
        return LbxExportJob(path, ("Chips", folder), title)

    def _weapon_job(self, path: str, siblings: dict[str, set[int]]) -> LbxExportJob | None:
        stem = PurePosixPath(path).stem
        if stem.casefold().startswith("lbx"):
            return self._part_job(path)
        match = re.fullmatch(r"wpn_([a-z]{2})_([a-z]{2})(\d{2})_(\d{2})", stem, re.I)
        if not match:
            return LbxExportJob(path, ("Weapons", "Others"), _safe_title(stem))
        weapon_type, subtype, number, variant = (part.casefold() for part in match.groups())
        folder, labels = _WEAPON_TYPES.get(weapon_type, ("Others", {}))
        label = labels.get(subtype, f"{subtype.upper()} Weapon")
        family = f"wpn_{weapon_type}_{subtype}{number}"
        variants = siblings.get(family, set())
        title = _numbered_variant_title(label, number, variant, variants)
        return LbxExportJob(
            path,
            ("Weapons", folder),
            title,
            aliases=self.weapon_aliases.get(stem, ()),
        )

    def build_jobs(self, *, romfs_prefix: str | None = None) -> list[LbxExportJob]:
        models = [
            entry.path for entry in self.entries
            if entry.path.casefold().endswith(".bcmdl")
            and entry.path.startswith(_MODEL_ROOTS)
            and PurePosixPath(entry.path).stem.casefold() != "wpn_fi_fi_sude"
            and not (
                entry.path.startswith("/3ddata/coreparts/")
                and re.search(
                    r"\d{2}_\d{2}_l$", PurePosixPath(entry.path).stem.casefold()
                ) is not None
            )
        ]
        siblings: dict[str, set[int]] = defaultdict(set)
        for path in models:
            stem = PurePosixPath(path).stem
            match = re.fullmatch(r"(.+?\d{2})_(\d{2})", stem)
            if match:
                siblings[match.group(1)].add(int(match.group(2)))

        tr_by_base = {
            path[:-9] + ".bcmdl": path
            for path in models
            if path.casefold().endswith("_tr.bcmdl")
        }
        custom_r_by_base: dict[str, str] = {}
        for path in models:
            if not path.startswith("/3ddata/parts/custom_r/"):
                continue
            stem = PurePosixPath(path).stem
            base_stem = stem[:-2] if stem.casefold().endswith("_r") else stem
            normal = f"/3ddata/parts/model/{base_stem}.bcmdl"
            if normal in models:
                custom_r_by_base[normal] = path
        jobs: list[LbxExportJob] = []
        for path in models:
            if path.casefold().endswith("_tr.bcmdl") or path.startswith(
                "/3ddata/parts/custom_r/"
            ):
                continue
            if path.startswith("/3ddata/coreparts/"):
                job = self._chip_job(path, siblings)
            elif path.startswith(_PART_ROOTS):
                job = self._part_job(path)
            else:
                job = self._weapon_job(path, siblings)
            if job is None:
                continue
            auxiliary = tr_by_base.get(path)
            custom_r = custom_r_by_base.get(path)
            if auxiliary or custom_r:
                job = LbxExportJob(
                    job.romfs_path,
                    job.category,
                    job.title,
                    custom_r_path=custom_r,
                    auxiliary_paths=(auxiliary,) if auxiliary else (),
                    aliases=job.aliases,
                )
            jobs.append(job)

        if romfs_prefix:
            prefix = "/" + romfs_prefix.strip("/")
            jobs = [job for job in jobs if any(path.startswith(prefix) for path in job.source_paths)]

        # A title collision means the ROM contains distinct source meshes. Keep
        # both and add a stable internal disambiguator instead of dropping data.
        grouped: dict[tuple[tuple[str, ...], str], list[LbxExportJob]] = defaultdict(list)
        for job in jobs:
            grouped[(job.category, job.title.casefold())].append(job)
        resolved: list[LbxExportJob] = []
        for same_title in grouped.values():
            def preference(item: LbxExportJob) -> tuple[int, str]:
                key, _component, _table, _transparent = self._part_identity(
                    PurePosixPath(item.romfs_path).stem
                )
                return (0 if key in self.robot_indices else 1, item.romfs_path)

            ordered = sorted(same_title, key=preference)
            for index, job in enumerate(ordered):
                title = job.title if index == 0 else f"{job.title} Variant {index}"
                resolved.append(LbxExportJob(
                    job.romfs_path,
                    job.category,
                    title,
                    custom_r_path=job.custom_r_path,
                    auxiliary_paths=job.auxiliary_paths,
                    aliases=job.aliases,
                ))
        return sorted(resolved, key=lambda job: (*job.category, job.title.casefold()))

    def job_for_path(self, romfs_path: str) -> LbxExportJob:
        match = next((job for job in self.build_jobs() if romfs_path in job.source_paths), None)
        if match is not None:
            return match
        stem = PurePosixPath(romfs_path).stem
        return LbxExportJob(romfs_path, ("Other",), _safe_title(stem))


@lru_cache(maxsize=4)
def load_lbx_catalog(rom_path: str) -> LbxCatalog:
    return LbxCatalog(rom_path)


def lbx_exportable_path(path: str) -> bool:
    lower = path.casefold()
    return lower.endswith(".bcmdl") and path.startswith(_MODEL_ROOTS)
