"""3DS asset magic table — routes these magics to the 3ds platform modules."""
from __future__ import annotations

ASSET_MAGICS: dict[str, str] = {
    "GFMD": "3ds",  # GFModel package descriptor
    "GFTX": "3ds",  # GFTexture set descriptor
    "FLIM": "3ds",  # BFLIM sprite descriptor
    "3DSR": "3ds",  # ROM summary
}
