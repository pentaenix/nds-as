"""Merged asset-magic routing table from all platform islands."""
from __future__ import annotations

from functools import lru_cache


@lru_cache(maxsize=1)
def all_asset_magics() -> dict[str, str]:
    table: dict[str, str] = {}
    for loader in (
        _nds_magics,
        _mobile_magics,
        _gba_magics,
        _gb_magics,
        _gbc_magics,
        _threeds_magics,
        _switch_magics,
    ):
        for magic, platform_id in loader().items():
            table[magic.upper()] = platform_id
    return table


def _nds_magics() -> dict[str, str]:
    from ...platforms.nds.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)


def _mobile_magics() -> dict[str, str]:
    from ...platforms.mobile.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)


def _gba_magics() -> dict[str, str]:
    from ...platforms.gba.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)


def _gb_magics() -> dict[str, str]:
    from ...platforms.gb.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)


def _gbc_magics() -> dict[str, str]:
    from ...platforms.gbc.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)


def _threeds_magics() -> dict[str, str]:
    from ...platforms.threeds.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)

def _switch_magics() -> dict[str, str]:
    from ...platforms.switch.magics import ASSET_MAGICS

    return dict(ASSET_MAGICS)

