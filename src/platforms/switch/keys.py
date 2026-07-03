"""Switch console key loading and key-area-key derivation.

RAE never ships Nintendo keys. To read a retail NSP/XCI you must supply the
``prod.keys`` file dumped from your own console (Lockpick_RCM output). We look
for it in the usual emulator locations and next to the ROM, or you can point at
one explicitly.

Everything here is standard AES: the key material is read from the text file,
not generated. Only ``prod.keys`` (master/key-area keys, header key) is needed
for standard-crypto content; personalised titles additionally need the ticket's
title key, which we read from the NSP's ``.tik`` when present.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


class SwitchKeyError(RuntimeError):
    """Raised when required key material is missing or malformed."""


_KEY_SEARCH_DIRS = (
    Path.home() / ".switch",
    Path.home() / "Library" / "Application Support" / "Ryujinx" / "system",
    Path.home() / "Library" / "Application Support" / "yuzu" / "keys",
    Path.home() / ".local" / "share" / "yuzu" / "keys",
    Path.home() / ".config" / "Ryujinx" / "system",
)


def _parse_keyfile(text: str) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().lower()
        value = value.strip()
        try:
            keys[name] = bytes.fromhex(value)
        except ValueError:
            continue
    return keys


def find_prod_keys(rom_path: str | Path | None = None) -> Path | None:
    """Locate ``prod.keys`` from the env var, next to the ROM, or emulator dirs."""
    env = os.environ.get("RAE_SWITCH_PROD_KEYS")
    if env and Path(env).is_file():
        return Path(env)
    candidates: list[Path] = []
    if rom_path is not None:
        rom = Path(rom_path)
        candidates.append(rom.with_name("prod.keys"))
        candidates.append(rom.parent / "prod.keys")
    for directory in _KEY_SEARCH_DIRS:
        candidates.append(directory / "prod.keys")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


@dataclass(slots=True)
class SwitchKeys:
    """Parsed key material plus derived key-area keys per generation."""

    raw: dict[str, bytes] = field(default_factory=dict)
    source: Path | None = None

    @classmethod
    def load(cls, rom_path: str | Path | None = None) -> "SwitchKeys":
        path = find_prod_keys(rom_path)
        if path is None:
            raise SwitchKeyError(
                "Switch prod.keys not found. Dump it from your console with "
                "Lockpick_RCM and place it next to the ROM, in ~/.switch/, or set "
                "RAE_SWITCH_PROD_KEYS to its path."
            )
        try:
            raw = _parse_keyfile(path.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:
            raise SwitchKeyError(f"Could not read {path}: {exc}") from exc
        if not raw:
            raise SwitchKeyError(f"{path} contained no usable keys.")
        return cls(raw=raw, source=path)

    def get(self, name: str) -> bytes:
        value = self.raw.get(name.lower())
        if value is None:
            raise SwitchKeyError(f"Missing key: {name}")
        return value

    def has(self, name: str) -> bool:
        return name.lower() in self.raw

    @property
    def header_key(self) -> bytes:
        # 32-byte AES-XTS key (two 16-byte tweak/data keys) for NCA headers.
        return self.get("header_key")

    def key_area_key(self, key_index: int, key_generation: int) -> bytes:
        """Return the key-area key for (application/ocean/system, generation).

        Prefer the pre-derived ``key_area_key_<type>_<gen>`` entries that
        Lockpick emits; fall back to deriving from ``master_key_<gen>`` +
        ``aes_kek_generation_source`` + ``key_area_key_*_source`` if only the
        base master keys are present.
        """
        key_type = {0: "application", 1: "ocean", 2: "system"}.get(key_index)
        if key_type is None:
            raise SwitchKeyError(f"Unknown key_area key index {key_index}")
        gen = max(key_generation - 1, 0) if key_generation else 0
        direct = f"key_area_key_{key_type}_{gen:02x}"
        if self.has(direct):
            return self.get(direct)
        return self._derive_key_area_key(key_type, gen)

    def _derive_key_area_key(self, key_type: str, gen: int) -> bytes:
        from Crypto.Cipher import AES

        master = self.raw.get(f"master_key_{gen:02x}")
        kek_gen = self.raw.get("aes_kek_generation_source")
        kak_source = self.raw.get(f"key_area_key_{key_type}_source")
        key_gen_source = self.raw.get("aes_key_generation_source")
        if not (master and kek_gen and kak_source and key_gen_source):
            raise SwitchKeyError(
                f"Cannot derive key_area_key_{key_type}_{gen:02x}; provide a "
                "complete prod.keys (missing master/source keys)."
            )
        # kek = AES-ECB(master, kek_gen); then AES-ECB(kek, kak_source) via the
        # generation source, matching the standard Switch key ladder.
        kek = AES.new(master, AES.MODE_ECB).decrypt(kek_gen)
        step = AES.new(kek, AES.MODE_ECB).decrypt(kak_source)
        return AES.new(step, AES.MODE_ECB).decrypt(key_gen_source)
