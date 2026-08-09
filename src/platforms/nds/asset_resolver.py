from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Iterable

from .nitro_names import extract_nitro_names, TextureMatch
from .scanner import Asset
from .texture_library import TextureLibrary
from .model_texture_resolver import resolve_model_textures

Progress = Callable[[str], None]

MODEL_ANIMATION_MAGICS = frozenset({"BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"})


def folder_sibling_assets(
    asset: Asset,
    assets: Iterable[Asset],
    *,
    allowed_magics: frozenset[str] | set[str] | None = None,
    limit: int = 16,
) -> list[Asset]:
    """Return assets in the same ROM folder or container archive as ``asset``."""
    allowed = set(allowed_magics or ())
    out: list[Asset] = []
    seen = {asset.asset_id}
    for candidate in assets:
        if candidate.asset_id in seen:
            continue
        if allowed and candidate.magic not in allowed:
            continue
        same_folder = bool(candidate.folder_key and candidate.folder_key == asset.folder_key)
        same_container = bool(
            asset.container_chain
            and candidate.container_chain
            and candidate.container_chain[: len(asset.container_chain)] == asset.container_chain
        )
        if not same_folder and not same_container:
            continue
        out.append(candidate)
        seen.add(candidate.asset_id)
        if len(out) >= limit:
            break
    return out


@dataclass(slots=True)
class RelatedAssetResult:
    assets: list[Asset]
    texture_candidates: int
    name_matched_textures: int
    model_names: set[str]


def build_related_assets(
    asset: Asset,
    assets: Iterable[Asset],
    *,
    pinned_texture_asset_id: str | None = None,
    limit: int = 64,
    progress: Progress | None = None,
    texture_library: TextureLibrary | None = None,
) -> RelatedAssetResult:
    """Find a bounded, deterministic set of siblings for model conversion.

    Normal texture resolution now uses exact NSBMD/NSBTX manifests. Path/name
    candidates remain available through debug helpers, but they are not treated as
    confirmed model textures.
    """
    all_assets = list(assets)
    useful_magics = {"BTX0", "BCA0", "BTA0", "BTP0", "BMA0", "BVA0", "BPC0"}
    chosen: list[Asset] = []
    seen: set[str] = {asset.asset_id}

    def log(text: str) -> None:
        if progress:
            progress(text)

    def add(candidate: Asset | None, reason: str = "") -> None:
        if candidate is None or candidate.asset_id in seen or candidate.magic not in useful_magics:
            return
        seen.add(candidate.asset_id)
        chosen.append(candidate)
        if reason and len(chosen) <= 12:
            log(f"Related asset: {candidate.magic} {candidate.virtual_path} ({reason})")

    log("Resolving deterministic texture/animation siblings off the UI thread...")

    pinned = None
    if pinned_texture_asset_id:
        pinned = next((a for a in all_assets if a.asset_id == pinned_texture_asset_id and a.magic == "BTX0"), None)

    if asset.magic == "BMD0":
        try:
            resolution = resolve_model_textures(
                asset,
                all_assets,
                texture_library=texture_library,
                manual_texture=pinned,
                defer_library_build=texture_library is None,
                progress=progress,
            )
            if texture_library is None and not resolution.verified:
                library = TextureLibrary.from_assets(all_assets)
                resolution = resolve_model_textures(asset, all_assets, texture_library=library, manual_texture=pinned, progress=progress)
            if resolution.resolved_assets:
                for tex in resolution.resolved_assets:
                    add(tex, f"{resolution.status}: exact decoded texture binding")
                log(f"Texture resolver: {resolution.status}; {len(resolution.decoded_images)} decoded image(s), {len(resolution.resolved_assets)} texture archive(s).")
            elif pinned is not None:
                add(pinned, "manual pinned texture archive")
                log("Texture resolver: no exact match; using the pinned BTX0 only as a manual override.")
            else:
                log("Texture resolver: unresolved. No fuzzy texture candidates will be applied automatically.")
        except Exception as exc:
            log(f"Texture resolver failed safely: {exc}")
            if pinned is not None:
                add(pinned, "manual pinned texture archive")

    # Animations/material animation siblings use same folder/container a lot.
    for candidate in all_assets:
        if len(chosen) >= limit:
            break
        if candidate.asset_id == asset.asset_id or candidate.magic == "BTX0" or candidate.magic not in useful_magics:
            continue
        if candidate.folder_key == asset.folder_key:
            add(candidate, "same folder")
    for candidate in all_assets:
        if len(chosen) >= limit:
            break
        if candidate.asset_id == asset.asset_id or candidate.magic == "BTX0" or candidate.magic not in useful_magics:
            continue
        if asset.container_chain and candidate.container_chain and asset.container_chain[-1:] == candidate.container_chain[-1:]:
            add(candidate, "same container")

    texture_candidates = sum(1 for a in chosen if a.magic == "BTX0")
    log(f"Using {len(chosen)} deterministic related asset(s): {texture_candidates} texture archive(s).")
    model_names = extract_nitro_names(asset.data) if asset.magic == "BMD0" else set()
    return RelatedAssetResult(chosen[:limit], texture_candidates, texture_candidates, model_names)

def texture_matches_for_model(
    asset: Asset,
    assets: Iterable[Asset],
    *,
    model_names: set[str] | None = None,
    limit: int = 16,
    progress: Progress | None = None,
) -> list[TextureMatch]:
    if asset.magic != "BMD0":
        return []
    model_names = model_names if model_names is not None else extract_nitro_names(asset.data)
    if not model_names:
        return []

    pool: list[Asset] = []
    seen: set[str] = set()

    def add(c: Asset) -> None:
        if c.magic == "BTX0" and c.asset_id not in seen:
            seen.add(c.asset_id)
            pool.append(c)

    all_assets = list(assets)
    for c in all_assets:
        if c.magic != "BTX0":
            continue
        if c.folder_key == asset.folder_key or (asset.container_chain and c.container_chain and asset.container_chain[-1:] == c.container_chain[-1:]):
            add(c)
    for c in pokemon_path_texture_candidates(asset, all_assets, limit=96):
        add(c)
    if not pool:
        for c in all_assets:
            if c.magic == "BTX0":
                add(c)
            if len(pool) >= 96:
                break

    if progress:
        progress(f"Checking {len(pool)} bounded BTX0 texture candidate(s) for name matches...")

    matches: list[TextureMatch] = []
    for i, candidate in enumerate(pool, start=1):
        if progress and (i == 1 or i % 24 == 0 or i == len(pool)):
            progress(f"Texture name matching {i}/{len(pool)}: {candidate.virtual_path}")
        overlap = sorted(model_names & extract_nitro_names(candidate.data))
        if overlap:
            matches.append(TextureMatch(asset=candidate, overlapping_names=tuple(overlap)))
    matches.sort(key=lambda m: (-len(m.overlapping_names), m.asset.virtual_path))
    return matches[:limit]


def pokemon_path_texture_candidates(asset: Asset, assets: Iterable[Asset], *, limit: int = 24) -> list[Asset]:
    path = asset.virtual_path.casefold()
    candidates = [a for a in assets if a.magic == "BTX0"]
    scored: list[tuple[int, str, Asset]] = []
    for candidate in candidates:
        cpath = candidate.virtual_path.casefold()
        score = 9999

        # B2W2/BW-style map model / texture split from community notes.
        if "a/0/0/8" in path and "a/0/1/4" in cpath:
            score = 0
        elif "a/0/0/8" in path and any(p in cpath for p in ("a/1/5/8", "a/1/7/4", "a/1/7/5", "a/1/8/7")):
            score = 4

        # Gen 4/HGSS map texture/model relation archives.
        elif any(p in path for p in ("area_build", "build_model", "bm_field", "bm_room", "a/0/4/0", "a/1/4/8")) and any(
            p in cpath for p in ("areabm_texset", "map_tex_set", "a/0/4/4", "a/0/7/0")
        ):
            score = 6

        # Battle/move effect graphics are not BTX0 in most Gen 4 cases, but this
        # fallback still helps generic games and Gen 5 texture-driven effects.
        elif any(k in path for k in ("battle", "waza", "move", "effect")) and any(k in cpath for k in ("battle", "waza", "move", "effect")):
            score = 20

        elif path.startswith("a/") and cpath.startswith("a/"):
            model_nums = path_numbers(path)
            tex_nums = path_numbers(cpath)
            if model_nums and tex_nums:
                nearest = min(abs(a - b) for a in model_nums for b in tex_nums)
                score = 80 + nearest

        if score < 9999:
            scored.append((score, candidate.virtual_path, candidate))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:limit]]


def path_numbers(path: str) -> list[int]:
    return [int(part) for part in re.findall(r"\d+", path)]
