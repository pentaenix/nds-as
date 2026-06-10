"""Composed main window from independent UI mixins."""
from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

from .asset_labels import AssetLabelsMixin
from .browser_table import BrowserTableMixin
from .browser_trees import BrowserTreesMixin
from .details import DetailsMixin
from .easyfind_actions import EasyFindActionsMixin
from .export_actions import ExportActionsMixin
from .filters import FiltersMixin
from .model_preview import ModelPreviewMixin
from .preview_actions import PreviewActionsMixin
from .rom_loader import RomLoaderMixin
from .selection import SelectionMixin
from .session_actions import SessionActionsMixin
from .shell import ShellMixin
from .texture_index import TextureIndexMixin
from .texture_assigner_panel import TextureAssignerPanelMixin
from .texture_animation_panel import TextureAnimationPanelMixin
from .texture_resolve import TextureResolveMixin


class MainWindow(
    QMainWindow,
    ShellMixin,
    FiltersMixin,
    BrowserTableMixin,
    BrowserTreesMixin,
    TextureIndexMixin,
    RomLoaderMixin,
    SelectionMixin,
    DetailsMixin,
    TextureResolveMixin,
    TextureAssignerPanelMixin,
    TextureAnimationPanelMixin,
    PreviewActionsMixin,
    SessionActionsMixin,
    EasyFindActionsMixin,
    ExportActionsMixin,
    ModelPreviewMixin,
    AssetLabelsMixin,
):
    """Desktop shell composed from feature mixins with shared instance state."""

    def __init__(self) -> None:
        QMainWindow.__init__(self)
        self.setup_shell()


__all__ = ["MainWindow"]
