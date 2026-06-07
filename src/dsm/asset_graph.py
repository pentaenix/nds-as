from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Callable, Iterable

from .nitro_names import extract_nitro_names
from .scanner import Asset
from .texture_library import TextureLibrary
from .model_texture_resolver import resolve_model_textures

Progress = Callable[[str], None]

TWO_D_MAGICS = {"RGCN", "RLCN", "RCSN", "RECN", "RNAN"}
MODEL_RELATED_MAGICS = {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}
AUDIO_MAGICS = {"SDAT", "SSEQ", "SSAR", "SBNK", "SWAR", "SWAV", "STRM"}


@dataclass(slots=True)
class AssetRelation:
    target_id: str
    relation: str
    score: int
    reason: str


@dataclass(slots=True)
class AssetGraph:
    relations: dict[str, list[AssetRelation]] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)

    def merge(self, other: "AssetGraph") -> None:
        for key, ids in other.groups.items():
            bucket = self.groups.setdefault(key, [])
            seen = set(bucket)
            for asset_id in ids:
                if asset_id not in seen:
                    bucket.append(asset_id)
                    seen.add(asset_id)
        for source_id, rows in other.relations.items():
            bucket = self.relations.setdefault(source_id, [])
            for row in rows:
                for existing in bucket:
                    if existing.target_id == row.target_id and existing.relation == row.relation:
                        if row.score > existing.score:
                            existing.score = row.score
                            existing.reason = row.reason
                        break
                else:
                    bucket.append(AssetRelation(row.target_id, row.relation, row.score, row.reason))

    def related_ids(self, asset_id: str, relation: str | None = None) -> list[str]:
        rows = self.relations.get(asset_id, [])
        if relation:
            rows = [r for r in rows if r.relation == relation]
        return [r.target_id for r in sorted(rows, key=lambda r: (-r.score, r.target_id))]

    def related_assets(self, asset: Asset, assets_by_id: dict[str, Asset], relation: str | None = None, limit: int = 64) -> list[Asset]:
        out: list[Asset] = []
        for rid in self.related_ids(asset.asset_id, relation=relation):
            found = assets_by_id.get(rid)
            if found is not None:
                out.append(found)
            if len(out) >= limit:
                break
        return out

    def relation_summary(self, asset_id: str, assets_by_id: dict[str, Asset], limit: int = 12) -> list[str]:
        lines = []
        for rel in sorted(self.relations.get(asset_id, []), key=lambda r: (-r.score, r.relation, r.target_id))[:limit]:
            asset = assets_by_id.get(rel.target_id)
            path = asset.virtual_path if asset else rel.target_id
            magic = asset.magic if asset else "?"
            lines.append(f"{rel.relation}: {magic} {path} ({rel.reason}, score {rel.score})")
        return lines


def build_asset_groups(assets: Iterable[Asset], progress: Progress | None = None) -> AssetGraph:
    """Build only the cheap grouping layer used by the tree UI.

    This intentionally does not match palettes/textures/models. Full relationship
    matching can be expensive on Pokémon ROMs and should run on demand for a
    selected asset.
    """
    all_assets = list(assets)
    graph = AssetGraph()
    if progress:
        progress(f"Building mapped folder tree for {len(all_assets)} asset(s)...")
    _build_groups(graph, all_assets)
    if progress:
        progress(f"Folder tree ready: {len(graph.groups)} mapped group(s). Relationship graph is lazy; use Build Relationships for selected assets.")
    return graph


def build_asset_graph(assets: Iterable[Asset], progress: Progress | None = None) -> AssetGraph:
    all_assets = list(assets)
    graph = build_asset_groups(all_assets, progress=None)

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    log("Building full asset relationship graph: palettes, textures, cells, animations, audio children...")
    _build_2d_relations(graph, all_assets)
    _build_model_relations(graph, all_assets, progress=progress)
    _build_audio_relations(graph, all_assets)
    total_rel = sum(len(v) for v in graph.relations.values())
    log(f"Full asset graph ready: {len(graph.groups)} folder/category group(s), {total_rel} relationship edge(s).")
    return graph


def build_asset_graph_for_selected(
    assets: Iterable[Asset],
    selected_ids: Iterable[str],
    progress: Progress | None = None,
    *,
    texture_library: TextureLibrary | None = None,
) -> AssetGraph:
    """Build relationship edges only for selected assets.

    The output can be merged into the window's existing graph. This is the fast
    interactive path for previews/exports: it avoids a global O(models*textures)
    pass while still giving one asset good palette/texture/animation candidates.
    """
    all_assets = list(assets)
    selected = [a for a in all_assets if a.asset_id in set(selected_ids)]
    graph = AssetGraph()

    def log(msg: str) -> None:
        if progress:
            progress(msg)

    log(f"Building relationship graph for {len(selected)} selected asset(s), not the whole ROM...")
    _build_groups(graph, selected)
    for idx, asset in enumerate(selected, start=1):
        log(f"Graph {idx}/{len(selected)}: {asset.magic or asset.kind} {asset.virtual_path}")
        if asset.magic in TWO_D_MAGICS:
            _build_2d_relations_for_asset(graph, asset, all_assets, progress=progress)
        elif asset.magic == "BMD0":
            _build_model_relations_for_asset(graph, asset, all_assets, progress=progress, texture_library=texture_library)
        elif asset.magic == "BTX0":
            _build_texture_relations_for_asset(graph, asset, all_assets, progress=progress)
        elif asset.magic in AUDIO_MAGICS:
            _build_audio_relations_for_asset(graph, asset, all_assets)
        else:
            log("No relationship matcher for this asset type yet; stored folder group only.")
    total_rel = sum(len(v) for v in graph.relations.values())
    log(f"Selected relationship graph ready: {total_rel} edge(s).")
    return graph


def _add(graph: AssetGraph, source: Asset, target: Asset, relation: str, score: int, reason: str) -> None:
    if source.asset_id == target.asset_id:
        return
    rows = graph.relations.setdefault(source.asset_id, [])
    for existing in rows:
        if existing.target_id == target.asset_id and existing.relation == relation:
            if score > existing.score:
                existing.score = score
                existing.reason = reason
            return
    rows.append(AssetRelation(target.asset_id, relation, score, reason))


def _build_groups(graph: AssetGraph, assets: list[Asset]) -> None:
    for asset in assets:
        key = friendly_group_key(asset)
        graph.groups.setdefault(key, []).append(asset.asset_id)


def friendly_group_key(asset: Asset) -> str:
    category = asset.mapping_category or "unknown"
    label = asset.mapping_label or "Detected by signature"
    folder = asset.folder_key or "/"
    # Device Manager-style tree: category -> mapped label -> real folder.
    return f"{category} / {label} / {folder}"


def _container_key(asset: Asset) -> str:
    if asset.container_chain:
        return asset.container_chain[-1]
    return asset.folder_key or ""


def _path_nums(asset: Asset) -> list[int]:
    return [int(x) for x in re.findall(r"\d+", asset.virtual_path)]


def _same_context_score(a: Asset, b: Asset) -> tuple[int, str]:
    if a.folder_key and a.folder_key == b.folder_key:
        return 80, "same folder"
    if _container_key(a) and _container_key(a) == _container_key(b):
        return 70, "same container/archive"
    # Numbered NARC entries often pair by index. This is only a fallback.
    an, bn = _path_nums(a), _path_nums(b)
    if an and bn:
        nearest = min(abs(x - y) for x in an[-3:] for y in bn[-3:])
        if nearest <= 2:
            return 55 - nearest, f"nearby numbered archive/index ({nearest})"
    return 0, ""


def _build_2d_relations(graph: AssetGraph, assets: list[Asset]) -> None:
    palettes = [a for a in assets if a.magic == "RLCN"]
    tiles = [a for a in assets if a.magic == "RGCN"]
    screens = [a for a in assets if a.magic == "RCSN"]
    cells = [a for a in assets if a.magic == "RECN"]
    animations = [a for a in assets if a.magic == "RNAN"]

    for tile in tiles:
        for pal in _rank_contextual(tile, palettes, limit=4):
            _add(graph, tile, pal[2], "palette", pal[0], pal[1])
            _add(graph, pal[2], tile, "tiles", pal[0], pal[1])
        for screen in _rank_contextual(tile, screens, limit=4):
            _add(graph, tile, screen[2], "tilemap", screen[0], screen[1])
            _add(graph, screen[2], tile, "tiles", screen[0], screen[1])
        for cell in _rank_contextual(tile, cells, limit=4):
            _add(graph, tile, cell[2], "cells", cell[0], cell[1])
            _add(graph, cell[2], tile, "tiles", cell[0], cell[1])
        for anim in _rank_contextual(tile, animations, limit=4):
            _add(graph, tile, anim[2], "animation", anim[0], anim[1])
            _add(graph, anim[2], tile, "tiles", anim[0], anim[1])

    # NCER cells often animate through NANR and use the same tile/palette pair.
    for cell in cells:
        for anim in _rank_contextual(cell, animations, limit=4):
            _add(graph, cell, anim[2], "animation", anim[0], anim[1])
            _add(graph, anim[2], cell, "cells", anim[0], anim[1])
        for pal in _rank_contextual(cell, palettes, limit=4):
            _add(graph, cell, pal[2], "palette", pal[0], pal[1])
        for tile in _rank_contextual(cell, tiles, limit=4):
            _add(graph, cell, tile[2], "tiles", tile[0], tile[1])

    for screen in screens:
        for pal in _rank_contextual(screen, palettes, limit=4):
            _add(graph, screen, pal[2], "palette", pal[0], pal[1])
        for tile in _rank_contextual(screen, tiles, limit=4):
            _add(graph, screen, tile[2], "tiles", tile[0], tile[1])


def _rank_contextual(source: Asset, candidates: list[Asset], *, limit: int) -> list[tuple[int, str, Asset]]:
    ranked: list[tuple[int, str, Asset]] = []
    for candidate in candidates:
        if candidate.asset_id == source.asset_id:
            continue
        score, reason = _same_context_score(source, candidate)
        if score:
            ranked.append((score, reason, candidate))
        elif source.mapping_category and source.mapping_category == candidate.mapping_category:
            ranked.append((35, "same mapped category", candidate))
    ranked.sort(key=lambda item: (-item[0], item[2].virtual_path))
    return ranked[:limit]


def _build_model_relations(graph: AssetGraph, assets: list[Asset], *, progress: Progress | None = None) -> None:
    models = [a for a in assets if a.magic == "BMD0"]
    related = [a for a in assets if a.magic in MODEL_RELATED_MAGICS]
    textures = [a for a in related if a.magic == "BTX0"]
    name_cache: dict[str, set[str]] = {}

    def names(asset: Asset) -> set[str]:
        if asset.asset_id not in name_cache:
            name_cache[asset.asset_id] = extract_nitro_names(asset.data)
        return name_cache[asset.asset_id]

    for idx, model in enumerate(models, start=1):
        if progress and (idx == 1 or idx % 25 == 0 or idx == len(models)):
            progress(f"Graph: matching model {idx}/{len(models)} against bounded texture/animation candidates...")
        model_names = names(model)
        # Name overlap: best possible general signal for NSBMD/NSBTX.
        checked = 0
        for tex in textures:
            if checked > 160:
                break
            context_score, context_reason = _same_context_score(model, tex)
            p = tex.virtual_path.casefold()
            m = model.virtual_path.casefold()
            pokemon_hint = 0
            if "a/0/0/8" in m and any(x in p for x in ("a/0/1/4", "a/1/5/8", "a/1/7/4", "a/1/7/5", "a/1/8/7")):
                pokemon_hint = 58
            elif any(x in m for x in ("area_build", "build_model", "bm_field", "bm_room")) and any(x in p for x in ("areabm_texset", "map_tex_set")):
                pokemon_hint = 58
            if not context_score and not pokemon_hint:
                continue
            checked += 1
            overlap = sorted(model_names & names(tex)) if model_names else []
            if overlap:
                _add(graph, model, tex, "texture", 100 + min(20, len(overlap)), "shared reliable Nitro material/texture names: " + ", ".join(overlap[:5]))
            else:
                score = max(context_score, pokemon_hint)
                reason = context_reason if context_score >= pokemon_hint else "Pokémon mapping texture archive hint"
                _add(graph, model, tex, "texture-candidate", score, reason)

        # Animations/material animation siblings use same folder/container a lot.
        for candidate in related:
            if candidate.magic == "BTX0" or candidate.asset_id == model.asset_id:
                continue
            score, reason = _same_context_score(model, candidate)
            if score:
                _add(graph, model, candidate, "animation", score, reason)


def _build_audio_relations(graph: AssetGraph, assets: list[Asset]) -> None:
    # Children extracted from SDAT/SWAR are represented as virtual children of the
    # archive path; this relation makes the UI/details tell that story clearly.
    containers = [a for a in assets if a.magic in {"SDAT", "SWAR"}]
    children = [a for a in assets if a.magic in AUDIO_MAGICS and a.container_chain]
    for parent in containers:
        pp = parent.virtual_path
        for child in children:
            if pp in child.container_chain:
                _add(graph, parent, child, "audio-child", 90, "extracted from archive")
                _add(graph, child, parent, "audio-parent", 90, "source archive")



def _build_2d_relations_for_asset(graph: AssetGraph, asset: Asset, assets: list[Asset], *, progress: Progress | None = None) -> None:
    def log(msg: str) -> None:
        if progress:
            progress(msg)
    palettes = [a for a in assets if a.magic == "RLCN"]
    tiles = [a for a in assets if a.magic == "RGCN"]
    screens = [a for a in assets if a.magic == "RCSN"]
    cells = [a for a in assets if a.magic == "RECN"]
    animations = [a for a in assets if a.magic == "RNAN"]
    if asset.magic == "RGCN":
        log(f"Finding palettes/tilemaps/cells/animations for tile graphics among {len(assets)} assets...")
        for pal in _rank_contextual(asset, palettes, limit=8):
            _add(graph, asset, pal[2], "palette", pal[0], pal[1]); _add(graph, pal[2], asset, "tiles", pal[0], pal[1])
        for screen in _rank_contextual(asset, screens, limit=8):
            _add(graph, asset, screen[2], "tilemap", screen[0], screen[1]); _add(graph, screen[2], asset, "tiles", screen[0], screen[1])
        for cell in _rank_contextual(asset, cells, limit=8):
            _add(graph, asset, cell[2], "cells", cell[0], cell[1]); _add(graph, cell[2], asset, "tiles", cell[0], cell[1])
        for anim in _rank_contextual(asset, animations, limit=8):
            _add(graph, asset, anim[2], "animation", anim[0], anim[1]); _add(graph, anim[2], asset, "tiles", anim[0], anim[1])
    elif asset.magic == "RLCN":
        log(f"Finding tile/cell/tilemap users for palette among {len(assets)} assets...")
        for tile in _rank_contextual(asset, tiles, limit=12):
            _add(graph, asset, tile[2], "tiles", tile[0], tile[1]); _add(graph, tile[2], asset, "palette", tile[0], tile[1])
        for cell in _rank_contextual(asset, cells, limit=8):
            _add(graph, asset, cell[2], "cells", cell[0], cell[1]); _add(graph, cell[2], asset, "palette", cell[0], cell[1])
        for screen in _rank_contextual(asset, screens, limit=8):
            _add(graph, asset, screen[2], "tilemap", screen[0], screen[1]); _add(graph, screen[2], asset, "palette", screen[0], screen[1])
    elif asset.magic == "RCSN":
        log("Finding tile graphics and palette candidates for tilemap...")
        for tile in _rank_contextual(asset, tiles, limit=8):
            _add(graph, asset, tile[2], "tiles", tile[0], tile[1]); _add(graph, tile[2], asset, "tilemap", tile[0], tile[1])
        for pal in _rank_contextual(asset, palettes, limit=8):
            _add(graph, asset, pal[2], "palette", pal[0], pal[1]); _add(graph, pal[2], asset, "tilemap", pal[0], pal[1])
    elif asset.magic == "RECN":
        log("Finding tile graphics, palettes, and animations for cell layout...")
        for tile in _rank_contextual(asset, tiles, limit=8):
            _add(graph, asset, tile[2], "tiles", tile[0], tile[1]); _add(graph, tile[2], asset, "cells", tile[0], tile[1])
        for pal in _rank_contextual(asset, palettes, limit=8):
            _add(graph, asset, pal[2], "palette", pal[0], pal[1]); _add(graph, pal[2], asset, "cells", pal[0], pal[1])
        for anim in _rank_contextual(asset, animations, limit=8):
            _add(graph, asset, anim[2], "animation", anim[0], anim[1]); _add(graph, anim[2], asset, "cells", anim[0], anim[1])
    elif asset.magic == "RNAN":
        log("Finding cell/tile/palette candidates for animation bank...")
        for cell in _rank_contextual(asset, cells, limit=8):
            _add(graph, asset, cell[2], "cells", cell[0], cell[1]); _add(graph, cell[2], asset, "animation", cell[0], cell[1])
        for tile in _rank_contextual(asset, tiles, limit=8):
            _add(graph, asset, tile[2], "tiles", tile[0], tile[1]); _add(graph, tile[2], asset, "animation", tile[0], tile[1])
        for pal in _rank_contextual(asset, palettes, limit=8):
            _add(graph, asset, pal[2], "palette", pal[0], pal[1]); _add(graph, pal[2], asset, "animation", pal[0], pal[1])


def _pokemon_texture_hint_score(model: Asset, tex: Asset) -> tuple[int, str]:
    p = tex.virtual_path.casefold()
    m = model.virtual_path.casefold()
    if "a/0/0/8" in m and any(x in p for x in ("a/0/1/4", "a/1/5/8", "a/1/7/4", "a/1/7/5", "a/1/8/7")):
        return 58, "Pokémon Gen 5 mapping texture archive hint"
    if any(x in m for x in ("area_build", "build_model", "bm_field", "bm_room")) and any(x in p for x in ("areabm_texset", "map_tex_set")):
        return 58, "Pokémon Gen 4 mapping texture archive hint"
    if model.mapping_category and tex.mapping_category and "model" in model.mapping_category and "texture" in tex.mapping_category:
        return 40, "mapped model/texture categories"
    return 0, ""


def _rank_model_texture_candidates(model: Asset, textures: list[Asset], *, limit: int = 192) -> list[tuple[int, str, Asset]]:
    ranked: list[tuple[int, str, Asset]] = []
    for tex in textures:
        context_score, context_reason = _same_context_score(model, tex)
        hint_score, hint_reason = _pokemon_texture_hint_score(model, tex)
        score = max(context_score, hint_score)
        reason = context_reason if context_score >= hint_score else hint_reason
        if score:
            ranked.append((score, reason, tex))
    ranked.sort(key=lambda item: (-item[0], item[2].virtual_path))
    return ranked[:limit]


def _build_model_relations_for_asset(
    graph: AssetGraph,
    model: Asset,
    assets: list[Asset],
    *,
    progress: Progress | None = None,
    texture_library: TextureLibrary | None = None,
) -> None:
    def log(msg: str) -> None:
        if progress:
            progress(msg)
    related = [a for a in assets if a.magic in MODEL_RELATED_MAGICS and a.asset_id != model.asset_id]
    log("Model graph: parsing model materials and NSBTX texture manifests for exact matches...")
    try:
        resolution = resolve_model_textures(
            model,
            assets,
            texture_library=texture_library,
            defer_library_build=texture_library is None,
            progress=progress,
        )
        if texture_library is None and resolution.status == "unresolved" and not resolution.decoded_images:
            library = TextureLibrary.from_assets(assets, progress=None)
            resolution = resolve_model_textures(model, assets, texture_library=library, progress=progress)
        log(f"Model graph texture status: {resolution.status}; decoded images: {len(resolution.decoded_images)}; resolved texture archives: {len(resolution.resolved_assets)}.")
        for tex in resolution.resolved_assets:
            _add(graph, model, tex, "texture", 150, "exact decoded NSBMD material → NSBTX texture/palette binding")
        if not resolution.resolved_assets:
            log("Model graph: no exact decoded texture binding. Fuzzy candidate edges were not added to avoid false positives.")
    except Exception as exc:
        log(f"Model graph: exact texture resolver failed safely: {exc}")

    anims = [a for a in related if a.magic != "BTX0"]
    added = 0
    for candidate in anims:
        score, reason = _same_context_score(model, candidate)
        if score:
            _add(graph, model, candidate, "animation", score, reason)
            added += 1
            if added >= 32:
                break
    log(f"Model graph: added {len(graph.relations.get(model.asset_id, []))} deterministic relation(s).")

def _build_texture_relations_for_asset(graph: AssetGraph, tex: Asset, assets: list[Asset], *, progress: Progress | None = None) -> None:
    def log(msg: str) -> None:
        if progress:
            progress(msg)
    models = [a for a in assets if a.magic == "BMD0"]
    tex_names = extract_nitro_names(tex.data)
    log(f"Texture graph: checking {len(models)} model(s) against selected texture archive; texture names found: {len(tex_names)}.")
    ranked: list[tuple[int, str, Asset]] = []
    for model in models:
        context_score, context_reason = _same_context_score(model, tex)
        hint_score, hint_reason = _pokemon_texture_hint_score(model, tex)
        score = max(context_score, hint_score)
        reason = context_reason if context_score >= hint_score else hint_reason
        if score:
            ranked.append((score, reason, model))
    ranked.sort(key=lambda item: (-item[0], item[2].virtual_path))
    for idx, (score, reason, model) in enumerate(ranked[:48], start=1):
        overlap: list[str] = []
        if tex_names and idx <= 32:
            overlap = sorted(tex_names & extract_nitro_names(model.data))
        if overlap:
            _add(graph, tex, model, "model", 100 + min(20, len(overlap)), "shared reliable Nitro material/texture names: " + ", ".join(overlap[:5]))
            _add(graph, model, tex, "texture", 100 + min(20, len(overlap)), "shared reliable Nitro material/texture names: " + ", ".join(overlap[:5]))
        else:
            _add(graph, tex, model, "model-candidate", score, reason)
            _add(graph, model, tex, "texture-candidate", score, reason)
    log(f"Texture graph: added {len(graph.relations.get(tex.asset_id, []))} relation(s).")


def _build_audio_relations_for_asset(graph: AssetGraph, asset: Asset, assets: list[Asset]) -> None:
    if asset.magic in {"SDAT", "SWAR"}:
        for child in assets:
            if child.asset_id != asset.asset_id and asset.virtual_path in child.container_chain:
                _add(graph, asset, child, "audio-child", 90, "extracted from archive")
                _add(graph, child, asset, "audio-parent", 90, "source archive")
    elif asset.container_chain:
        for parent in assets:
            if parent.magic in {"SDAT", "SWAR"} and parent.virtual_path in asset.container_chain:
                _add(graph, asset, parent, "audio-parent", 90, "source archive")
                _add(graph, parent, asset, "audio-child", 90, "extracted from archive")

def graph_to_manifest(graph: AssetGraph, asset: Asset, assets_by_id: dict[str, Asset]) -> dict:
    return {
        "assetId": asset.asset_id,
        "path": asset.virtual_path,
        "magic": asset.magic,
        "relations": [
            {
                "relation": rel.relation,
                "score": rel.score,
                "reason": rel.reason,
                "targetId": rel.target_id,
                "targetPath": assets_by_id.get(rel.target_id).virtual_path if assets_by_id.get(rel.target_id) else None,
                "targetMagic": assets_by_id.get(rel.target_id).magic if assets_by_id.get(rel.target_id) else None,
            }
            for rel in sorted(graph.relations.get(asset.asset_id, []), key=lambda r: (-r.score, r.relation))
        ],
    }
