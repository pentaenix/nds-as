"""3DS asset magic table — routes these magics to the 3ds platform modules."""
from __future__ import annotations

ASSET_MAGICS: dict[str, str] = {
    "GFMD": "3ds",  # GFModel package descriptor
    "GFTX": "3ds",  # GFTexture set descriptor
    "FLIM": "3ds",  # BFLIM sprite descriptor
    "3DSR": "3ds",  # ROM summary
    "CGMD": "3ds",  # Named NintendoWare CGFX model/resource
    "CGTX": "3ds",  # Named NintendoWare CGFX texture
    "CGSA": "3ds",  # Named NintendoWare skeletal animation
    "CGMA": "3ds",  # Named NintendoWare material animation
    "CGCA": "3ds",  # Named NintendoWare camera animation
}
