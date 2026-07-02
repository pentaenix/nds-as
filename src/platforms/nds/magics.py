"""NDS / Nitro asset magic → platform id (owned by NDS island)."""
from __future__ import annotations

ASSET_MAGICS: dict[str, str] = {
    "BMD0": "nds",
    "BCA0": "nds",
    "BTA0": "nds",
    "BTP0": "nds",
    "BHA0": "nds",
    "BTX0": "nds",
    "RGCN": "nds",
    "RLCN": "nds",
    "RCSN": "nds",
    "RECN": "nds",
    "RNAN": "nds",
    "NFTR": "nds",
    "SDAT": "nds",
    "SSEQ": "nds",
    "SSAR": "nds",
    "SBNK": "nds",
    "SWAR": "nds",
    "SWAV": "nds",
    "STRM": "nds",
    "PNG": "nds",
}
