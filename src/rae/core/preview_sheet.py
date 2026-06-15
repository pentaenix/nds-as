"""Helpers for multi-entry texture / sprite sheet previews."""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..scanner import Asset

SHEET_PREVIEW_MAGICS = frozenset({"BTX0", "RGCN", "RCSN", "RECN", "RNAN"})


def asset_supports_sheet_preview(asset: Asset | None) -> bool:
    if asset is None:
        return False
    return str(getattr(asset, "magic", "") or "") in SHEET_PREVIEW_MAGICS


def sheet_preview_is_active(
    asset: Asset | None,
    *,
    sheet_asset_id: str | None,
    sheet_entries: list[dict[str, Any]],
) -> bool:
    if asset is None or not sheet_asset_id or len(sheet_entries) < 2:
        return False
    if not asset_supports_sheet_preview(asset):
        return False
    return str(asset.asset_id) == str(sheet_asset_id)


def safe_sheet_entry_filename(name: str) -> str:
    safe = re.sub(r"[^\w.\-]+", "_", str(name or "").strip()) or "entry"
    return safe[:120]
