"""Windows CD/ISO asset magic table."""
from __future__ import annotations

ASSET_MAGICS: dict[str, str] = {
    "WISO": "windows_iso",  # recognized disc summary descriptor
    "WILK": "windows_iso",  # setup-required / informational descriptor
    "WSMO": "windows_iso",  # Marine Park Empire static SMO model descriptor
    "WAM1": "windows_iso",  # Marine Park Empire animated AM1 model descriptor
}
