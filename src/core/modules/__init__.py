"""Platform capability modules — scan, model, texture, audio per console."""

from .registry import PlatformModules, asset_platform_id, get_platform_modules
from .types import ExportRoute, PreviewRoute, ProfileSummary, PreviewContext

__all__ = [
    "ExportRoute",
    "PlatformDispatch",
    "PlatformModules",
    "PreviewContext",
    "PreviewRoute",
    "ProfileSummary",
    "asset_platform_id",
    "dispatch_for_asset",
    "dispatch_for_path",
    "get_platform_modules",
]


def __getattr__(name: str):
    if name in {"PlatformDispatch", "dispatch_for_asset", "dispatch_for_path"}:
        from .dispatch import PlatformDispatch, dispatch_for_asset, dispatch_for_path

        return {
            "PlatformDispatch": PlatformDispatch,
            "dispatch_for_asset": dispatch_for_asset,
            "dispatch_for_path": dispatch_for_path,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
