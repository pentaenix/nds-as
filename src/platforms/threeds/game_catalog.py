"""Known 3DS cartridge product codes for game-specific features."""
from __future__ import annotations

from pathlib import Path

from .container import ThreedsImage

# Pokémon Ultra Moon — all retail regions share the A2B* product family.
ULTRA_MOON_PRODUCT_CODES: frozenset[str] = frozenset(
    {
        "CTR-P-A2BA",  # US
        "CTR-P-A2BP",  # EU
        "CTR-P-A2CJ",  # JP
        "CTR-P-A2BK",  # KR
        "CTR-P-A2BC",  # CN (Traditional)
    }
)


def is_ultra_moon_product_code(product_code: str) -> bool:
    code = (product_code or "").strip().upper()
    return code in ULTRA_MOON_PRODUCT_CODES


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


def is_ultra_moon_rom(rom_path: str | Path) -> bool:
    return is_ultra_moon_product_code(read_product_code(rom_path))
