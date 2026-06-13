"""Texture sheet tab: browse entries decoded from multi-texture / sprite sheets."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.preview_sheet import sheet_preview_is_active
from ...scanner import Asset
from ..preview.texture_sheet import SheetEntry, TextureSheetWidget


class TextureSheetPanelMixin:
    def _init_texture_sheet_state(self) -> None:
        self._sheet_preview_entries: list[dict[str, Any]] = []
        self._sheet_preview_asset_id: str | None = None

    def _clear_sheet_preview(self) -> None:
        self._sheet_preview_entries = []
        self._sheet_preview_asset_id = None
        if hasattr(self, "texture_sheet"):
            self.texture_sheet.set_context(asset_id=None, entries=[])

    def _set_sheet_preview(self, asset_id: str, entries: list[dict[str, Any]]) -> None:
        self._sheet_preview_asset_id = asset_id
        self._sheet_preview_entries = list(entries)
        self._refresh_texture_sheet()

    def _sheet_preview_is_active(self, asset: Asset | None = None) -> bool:
        asset = asset or self.selected_asset()
        return sheet_preview_is_active(
            asset,
            sheet_asset_id=self._sheet_preview_asset_id,
            sheet_entries=self._sheet_preview_entries,
        )

    def _refresh_texture_sheet(self, asset: Asset | None = None) -> None:
        widget: TextureSheetWidget | None = getattr(self, "texture_sheet", None)
        if widget is None:
            return
        asset = asset or self.selected_asset()
        if not self._sheet_preview_is_active(asset):
            widget.set_context(asset_id=None, entries=[])
            return
        entries = [
            SheetEntry(
                key=str(item.get("key") or item.get("name") or path),
                label=str(item.get("label") or item.get("name") or Path(path).stem),
                path=Path(path),
            )
            for item in self._sheet_preview_entries
            if (path := str(item.get("path") or "")).strip()
        ]
        widget.set_context(asset_id=asset.asset_id if asset else None, entries=entries)

    def _on_texture_sheet_entry_selected(self, path: str, label: str) -> None:
        preview = self.preview
        caption = f"{label}\n{path}"
        preview.show_image_path(Path(path), caption)
        asset = self.selected_asset()
        if asset is not None:
            self._refresh_texture_sheet(asset)
