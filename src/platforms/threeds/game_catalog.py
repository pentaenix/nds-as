"""Known 3DS cartridge product codes for game-specific features."""
from __future__ import annotations

from pathlib import Path

from .container import ThreedsImage

# Pokémon Ultra Moon — retail + eShop share the A2B* four-letter serial family.
# GameTDB: A2BA (generic), A2BE (NTSC-U), A2BJ (NTSC-J), A2BK (NTSC-K), A2BP (PAL).
ULTRA_MOON_SERIAL_PREFIX = "A2B"

# Explicit product codes (cart + eShop prefixes) kept for compatibility.
ULTRA_MOON_PRODUCT_CODES: frozenset[str] = frozenset(
    {
        "CTR-P-A2BA",
        "CTR-P-A2BE",  # US / NTSC-U cartridge
        "CTR-P-A2BP",  # EU / PAL
        "CTR-P-A2BJ",  # JP
        "CTR-P-A2BK",  # KR
        "CTR-P-A2BC",  # CN (Traditional)
        "CTR-N-A2BA",
        "CTR-N-A2BE",
        "CTR-N-A2BP",
        "CTR-N-A2BJ",
        "CTR-N-A2BK",
    }
)


def parse_product_serial(product_code: str) -> str:
    """Return the four-letter game serial from an NCCH product code (e.g. A2BE)."""
    code = (product_code or "").strip().upper()
    if not code:
        return ""
    if len(code) == 4 and code.isalnum():
        return code
    parts = code.split("-")
    tail = parts[-1] if parts else ""
    if len(tail) == 4 and tail.isalnum():
        return tail
    return ""


def is_ultra_moon_serial(serial: str) -> bool:
    s = (serial or "").strip().upper()
    return len(s) == 4 and s.startswith(ULTRA_MOON_SERIAL_PREFIX)


def is_ultra_moon_product_code(product_code: str) -> bool:
    code = (product_code or "").strip().upper()
    if code in ULTRA_MOON_PRODUCT_CODES:
        return True
    return is_ultra_moon_serial(parse_product_serial(code))


def read_product_code(rom_path: str | Path) -> str:
    path = Path(rom_path).expanduser().resolve()
    try:
        with ThreedsImage(path) as image:
            part = image.main_partition()
            if part is not None:
                return part.product_code
    except Exception:
        pass
    return ""


def is_ultra_moon_rom(rom_path: str | Path, *, product_code: str | None = None) -> bool:
    code = (product_code or "").strip() or read_product_code(rom_path)
    return is_ultra_moon_product_code(code)
