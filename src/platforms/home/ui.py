from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

from ..mobile.rom import scan_mobile_rom_path
from .library import build_home_library, home_packages_as_rae_assets


def open_home_source_in_window(window) -> None:
    path = QFileDialog.getExistingDirectory(
        window,
        "Open Pokémon HOME Android/cache source folder",
        str(Path.home()),
    )
    if not path:
        return
    try:
        window._update_status(f"Scanning Pokémon HOME source: {path}")
        assets = scan_mobile_rom_path(path, progress=window._update_status)
    except Exception as exc:
        QMessageBox.critical(window, "Pokémon HOME scan failed", str(exc))
        return
    window.rom_path = path
    window._rom_platform_id = "mobile"
    window.assets = assets
    window.visible_assets = []
    window.assets_by_id = {a.asset_id: a for a in assets}
    window.current_mapping = None
    window.rom_game_code = "HOME"
    window.rom_title = "Pokémon HOME Android source"
    window.session_path = None
    if hasattr(window, "_clear_texture_caches"):
        window._clear_texture_caches()
    if hasattr(window, "_asset_search_text"):
        window._asset_search_text.clear()
    if hasattr(window, "_mapped_tree_parts_by_id"):
        window._mapped_tree_parts_by_id.clear()
    if hasattr(window, "_raw_tree_parts_by_id"):
        window._raw_tree_parts_by_id.clear()
    if hasattr(window, "_browser_tab_versions"):
        window._browser_tab_versions.clear()
    if hasattr(window, "_filter_generation"):
        window._filter_generation += 1
    if hasattr(window, "table"):
        window.table.setRowCount(0)
    if hasattr(window, "tree"):
        window.tree.clear()
    if hasattr(window, "raw_tree"):
        window.raw_tree.clear()
    home_count = sum(1 for asset in assets if asset.magic == "HOME")
    if hasattr(window, "details"):
        if home_count:
            window.details.setPlainText(
                f"Pokémon HOME scan complete.\n\n"
                f"Form packages: {home_count:,}\n"
                f"Total browsable rows: {len(assets):,}\n\n"
                "Select a HOME package row for species completeness, or browse raw ABA/UNITY files in Raw Folders."
            )
        else:
            lib = build_home_library(path, progress=window._update_status, inspect_unity=False)
            window.details.setPlainText(_home_overview_text(lib.to_dict()))
    if hasattr(window, "preview"):
        window.preview.show_message(
            "Pokémon HOME source loaded. Select a HOME package row or an ABA/UNITY asset to preview.\n\n"
            "Open each species once in the HOME app to populate Cache textures for mesh preview."
        )
    if hasattr(window, "_rebuild_asset_filter_indexes"):
        window._rebuild_asset_filter_indexes()
    if hasattr(window, "_rebuild_show_types_menu"):
        window._rebuild_show_types_menu()
    if hasattr(window, "_apply_filter_result") and hasattr(window, "_compute_filtered_assets"):
        generation = window._filter_generation
        window._apply_filter_result(generation, window._compute_filtered_assets())
    elif hasattr(window, "apply_filter"):
        window.apply_filter()
    if hasattr(window, "_focus_browser_on_rom_folders"):
        window._focus_browser_on_rom_folders()
    window._update_status(f"Loaded {len(assets):,} Pokémon HOME asset row(s) ({home_count:,} form packages).")


def home_asset_details(asset) -> str | None:
    if getattr(asset, "magic", "") != "HOME":
        return None
    try:
        pkg = json.loads(asset.data.decode("utf-8"))
    except Exception:
        return "Pokémon HOME package\n\nCould not decode package manifest."
    lines = [
        f"Pokémon HOME package: {pkg.get('name')} ({pkg.get('id')})",
        f"National Dex: {pkg.get('number')}",
        f"Form: {pkg.get('form_a')}_{pkg.get('form_b')}",
        "",
        "Completeness",
    ]
    for key, label in (("model","Model"),("textures","Textures"),("skeletonRig","Skeleton/Rig"),("animations","Animations"),("idle","Idle"),("physicalAttack","Physical attack"),("specialAttack","Special attack")):
        lines.append(f"  {label}: {pkg.get('status', {}).get(key, 'unknown')}")
    files = pkg.get("source_files") or []
    if files:
        lines.extend(["", f"Source files: {len(files)}"])
        for row in files[:12]:
            lines.append(f"  {row.get('virtual_path')}")
        if len(files) > 12:
            lines.append(f"  … and {len(files) - 12} more")
    warnings = pkg.get("warnings") or []
    if warnings:
        lines.extend(["", "Warnings"])
        for warning in warnings[:8]:
            lines.append(f"  {warning}")
    return "\n".join(lines)


def _home_overview_text(data: dict) -> str:
    packages = data.get("packages", [])
    lines = [
        "Pokémon HOME source overview",
        f"Source: {data.get('source')}",
        f"Package id: {data.get('packageId') or 'unknown'}",
        f"UnityPy available: {data.get('unityPyAvailable')}",
        f"Pokémon form packages: {len(packages):,}",
        f"Ungrouped files: {len(data.get('ungroupedFiles') or []):,}",
        "",
        "This patch inventories Unity/HOME packages and exposes animation candidates. Full mesh+texture+animation viewport playback depends on readable Unity bundles plus UnityPy/AssetStudio/Blender export support; encrypted .aba/.abap packages are detected but not bypassed.",
    ]
    warnings = data.get("warnings") or []
    if warnings:
        lines.extend(["", "Warnings"])
        for warning in warnings[:10]:
            lines.append(f"  {warning}")
    return "\n".join(lines)
