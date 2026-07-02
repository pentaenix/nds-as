"""EasyFind asset identity and matching helpers."""
from __future__ import annotations

from ..scanner import Asset
from .models import EasyFindAssetRef, EasyFindMatchReport


def _weak_key(asset: EasyFindAssetRef) -> tuple[str, str]:
    return (asset.virtual_path, asset.magic)


def match_easyfind_assets(
    current_assets: list[Asset],
    easyfind_assets: list[EasyFindAssetRef],
) -> EasyFindMatchReport:
    """Match loaded assets to EasyFind asset references."""
    by_id: dict[str, Asset] = {a.asset_id: a for a in current_assets}
    by_path_magic_size: dict[tuple[str, str, int], list[Asset]] = {}
    by_rom_magic_size: dict[tuple[int, int, str, int], list[Asset]] = {}
    by_path_magic: dict[tuple[str, str], list[Asset]] = {}

    for asset in current_assets:
        by_path_magic_size.setdefault(
            (asset.virtual_path, asset.magic, asset.size), []
        ).append(asset)
        if asset.rom_file_id is not None and asset.rom_offset is not None:
            by_rom_magic_size.setdefault(
                (asset.rom_file_id, asset.rom_offset, asset.magic, asset.size), []
            ).append(asset)
        by_path_magic.setdefault(
            (asset.virtual_path, asset.magic), []
        ).append(asset)

    matched: dict[str, str] = {}
    missing: list[str] = []
    ambiguous: dict[str, list[str]] = {}

    for ef_asset in easyfind_assets:
        ef_id = ef_asset.asset_id

        if ef_id in by_id:
            matched[ef_id] = ef_id
            continue

        key_pms = (ef_asset.virtual_path, ef_asset.magic, ef_asset.size)
        candidates = by_path_magic_size.get(key_pms, [])
        if len(candidates) == 1:
            matched[ef_id] = candidates[0].asset_id
            continue
        if len(candidates) > 1:
            ambiguous[ef_id] = [c.asset_id for c in candidates]
            continue

        if ef_asset.rom_file_id is not None and ef_asset.rom_offset is not None:
            key_rom = (
                ef_asset.rom_file_id,
                ef_asset.rom_offset,
                ef_asset.magic,
                ef_asset.size,
            )
            rom_candidates = by_rom_magic_size.get(key_rom, [])
            if len(rom_candidates) == 1:
                matched[ef_id] = rom_candidates[0].asset_id
                continue
            if len(rom_candidates) > 1:
                ambiguous[ef_id] = [c.asset_id for c in rom_candidates]
                continue

        weak_candidates = by_path_magic.get(_weak_key(ef_asset), [])
        if len(weak_candidates) == 1:
            matched[ef_id] = weak_candidates[0].asset_id
            continue
        if len(weak_candidates) > 1:
            ambiguous[ef_id] = [c.asset_id for c in weak_candidates]
            continue

        missing.append(ef_id)

    total = len(easyfind_assets)
    matched_count = len(matched)
    summary = (
        f"Matched {matched_count}/{total} EasyFind assets. "
        f"Missing: {len(missing)}. Ambiguous: {len(ambiguous)}."
    )
    return EasyFindMatchReport(
        matched=matched,
        missing=missing,
        ambiguous=ambiguous,
        summary=summary,
    )
