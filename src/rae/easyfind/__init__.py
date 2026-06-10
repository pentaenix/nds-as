"""EasyFind discovery index format and APIs."""
from __future__ import annotations

from .build_index import classify_asset_magic, create_easyfind_document
from .format import (
    EASYFIND_EXTENSION,
    EASYFIND_FORMAT,
    EASYFIND_SCHEMA_VERSION,
)
from .identity import match_easyfind_assets
from .paths import (
    easyfind_path_for_game_code,
    easyfind_store_dir,
    is_valid_game_code,
    normalize_game_code,
    read_nds_rom_identity,
)
from .models import (
    EasyFindAssetRef,
    EasyFindDocument,
    EasyFindIdentity,
    EasyFindManifest,
    EasyFindMatchReport,
    EasyFindNode,
    EasyFindPreviewRef,
    EasyFindQuickOpen,
    EasyFindValidationReport,
)
from .store import (
    load_easyfind,
    load_easyfind_quick_open,
    read_easyfind_preview,
    save_easyfind,
)
from .validation import (
    EasyFindCorruptError,
    EasyFindError,
    EasyFindUnsupportedFormatError,
    EasyFindValidationError,
    validate_easyfind,
)

__all__ = [
    "EASYFIND_EXTENSION",
    "EASYFIND_FORMAT",
    "EASYFIND_SCHEMA_VERSION",
    "EasyFindAssetRef",
    "EasyFindCorruptError",
    "EasyFindDocument",
    "EasyFindError",
    "EasyFindIdentity",
    "EasyFindManifest",
    "EasyFindMatchReport",
    "EasyFindNode",
    "EasyFindPreviewRef",
    "EasyFindQuickOpen",
    "EasyFindUnsupportedFormatError",
    "EasyFindValidationError",
    "EasyFindValidationReport",
    "classify_asset_magic",
    "create_easyfind_document",
    "easyfind_path_for_game_code",
    "easyfind_store_dir",
    "is_valid_game_code",
    "normalize_game_code",
    "read_nds_rom_identity",
    "load_easyfind",
    "load_easyfind_quick_open",
    "match_easyfind_assets",
    "read_easyfind_preview",
    "save_easyfind",
    "validate_easyfind",
]
