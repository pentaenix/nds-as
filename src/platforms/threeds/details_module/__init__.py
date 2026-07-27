from __future__ import annotations

import json

from ....core.assets import Asset
from ..rom import load_descriptor


class ThreedsDetailsModule:
    platform_id = "3ds"

    def asset_details(self, asset: Asset) -> str | None:
        descriptor = load_descriptor(asset)
        if not descriptor:
            return None
        kind = descriptor.get("type")
        lines: list[str] = []
        if kind == "model":
            lines.append(f"3DS Pokémon model — {descriptor.get('name', '?')}")
            lines.append(f"GARC {descriptor.get('garc')} group {descriptor.get('group')} (slots {descriptor.get('base_slot')}+)")
            lines.append("Export options: GLB, lossless GLBZ (optional shiny checkbox), texture PNGs, raw animation packs.")
        elif kind == "textures":
            lines.append(f"3DS texture set — {descriptor.get('name', '?')}")
            lines.append("Contains the normal set; the shiny set lives in the next GARC slot.")
            lines.append("Export decodes both to PNG.")
        elif kind == "world_model":
            lines.append(f"3DS world model — {descriptor.get('name', '?')}")
            lines.append(f"GARC {descriptor.get('garc')} slot {descriptor.get('slot')}")
            compositions = descriptor.get("world_compositions") or []
            if compositions:
                lines.append("Complete-map compositions:")
                for item in compositions:
                    slots = " + ".join(f"{int(slot):04d}" for slot in item.get("slots") or [])
                    default = " (default preview)" if item.get("id") == descriptor.get("composition_id") else ""
                    lines.append(f"- {item.get('label', item.get('id', '?'))}: slots {slots}{default}")
                lines.append("Export options include each complete GLB/GLBZ/texture set, this layer by itself, and the backend Attend environment catalog profile.")
            else:
                lines.append("No separate outer map is cataloged; this includes self-contained/closed maps.")
                lines.append("Export options: GLB, lossless GLBZ, texture PNGs, Attend environment catalog, raw payload.")
        elif kind == "texture_bank":
            lines.append(f"3DS texture — {descriptor.get('name', '?')}")
            lines.append(f"GARC {descriptor.get('garc')} slot {descriptor.get('slot')}")
            lines.append("Export decodes every GFTexture in the slot to PNG.")
        elif kind == "sprite":
            lines.append(f"3DS BFLIM sprite #{descriptor.get('sprite_index')}")
            lines.append(f"GARC {descriptor.get('garc')}")
        elif kind == "summary":
            lines.append(json.dumps(descriptor, indent=2, ensure_ascii=False))
        return "\n".join(lines) if lines else None

    def preview_status_lines(self, asset: Asset, *, window: object) -> list[str]:
        return []
