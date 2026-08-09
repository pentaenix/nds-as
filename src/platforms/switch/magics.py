"""Nintendo Switch asset magic table."""
from __future__ import annotations

ASSET_MAGICS: dict[str, str] = {
    "SWRM": "switch",  # ROM summary descriptor
    "SWLK": "switch",  # locked ROM (keys required)
    "TRMD": "switch",  # Trinity model (.trmdl)
    "BNTX": "switch",  # texture container
    "TRAN": "switch",  # animation (.tranm / .gfbanm)
    "TRPF": "switch",  # Trinity pack (.trpfs / .trpfd)
    "WWSE": "switch",  # Wwise audio bank
    "BIK2": "switch",  # Bink movie
    "SWFL": "switch",  # other RomFS file
}
