from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QFileDialog, QMessageBox

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
        lib = build_home_library(path, progress=window._update_status, inspect_unity=True)
        assets = home_packages_as_rae_assets(lib)
    except Exception as exc:
        QMessageBox.critical(window, "Pokémon HOME scan failed", str(exc))
        return
    window.rom_path = path
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
    if hasattr(window, "details"):
        window.details.setPlainText(_home_overview_text(lib.to_dict()))
    if hasattr(window, "preview"):
        window.preview.show_message("Pokémon HOME package list loaded. Select a HOME row to inspect model/texture/rig/animation status.\n\nInstall UnityPy in the RAE venv for object-level bundle inspection.")
    if hasattr(window, "_rebuild_asset_filter_indexes"):
        window._rebuild_asset_filter_indexes()
    if hasattr(window, "_rebuild_show_types_menu"):
        window._rebuild_show_types_menu()
    if hasattr(window, "apply_filter"):
        window.apply_filter()
    window._update_status(f"Loaded {len(assets):,} Pokémon HOME form package(s).")


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
    lines.extend(["", "Source files"])
    for row in pkg.get("source_files", [])[:80]:
        lines.append(f"  - {row.get('classification')}: {row.get('virtual_path')} ({row.get('magic') or 'no magic'})")
    objects = pkg.get("object_counts", {})
    if objects:
        lines.extend(["", "Unity object counts"])
        for key, value in sorted(objects.items()):
            lines.append(f"  {key}: {value}")
    anims = pkg.get("animation_candidates", {})
    if anims:
        lines.extend(["", "Animation candidates"])
        for bucket in ("idle", "physical_attack", "special_attack", "unmapped"):
            rows = anims.get(bucket, [])
            if not rows:
                continue
            lines.append(f"  {bucket}:")
            for item in rows[:20]:
                lines.append(f"    - {item.get('name') or '(unnamed clip)'}  confidence={item.get('confidence')}  {Path(item.get('bundle','')).name}")
    warnings = pkg.get("warnings", [])
    if warnings:
        lines.extend(["", "Warnings"])
        lines.extend(f"  - {w}" for w in warnings[:20])
    lines.extend([
        "",
        "Preview note",
        "  This patch inventories Unity/HOME packages and exposes animation candidates. Full mesh+texture+animation viewport playback depends on readable Unity bundles plus UnityPy/AssetStudio/Blender export support; encrypted .aba/.abap packages are detected but not bypassed.",
    ])
    return "\n".join(lines)


def _home_overview_text(data: dict) -> str:
    packages = data.get("packages", [])
    return "\n".join([
        "Pokémon HOME source overview",
        f"Source: {data.get('source')}",
        f"Package id: {data.get('packageId') or 'unknown'}",
        f"UnityPy available: {data.get('unityPyAvailable')}",
        f"Pokémon form packages: {len(packages):,}",
        "",
        "Select a package row to see source files, Unity object counts, dependency clues, and animation candidates.",
    ])
