"""EasyFind build and status panel."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...easyfind import EasyFindQuickOpen, EasyFindValidationReport
from ..constants import CHROME_BUTTON_STYLE

MODAL_STYLE = """
QFrame#easyfindModal {
    background-color: #2f2f2f;
    border: 1px solid #4a4a4a;
    border-radius: 10px;
}
"""

TITLE_STYLE = "font-size: 18px; font-weight: 600; color: #f0f0f0;"
BODY_STYLE = "font-size: 13px; color: #c8c8c8;"
WARNING_STYLE = "font-size: 12px; color: #d9a866;"
DETAIL_STYLE = "font-size: 12px; color: #a8a8a8; font-family: Menlo, Consolas, monospace;"
STAGE_STYLE = "font-size: 12px; color: #b0b0b0;"
SNAPSHOT_LABEL_STYLE = "font-size: 12px; color: #9a9a9a;"

MODAL_WIDTH = 500
MODAL_MARGIN = 48
SNAPSHOT_SIZE = 256


class EasyFindBuildPanel(QWidget):
    """Centered modal card over the grid canvas."""

    validate_requested = Signal()
    build_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._current_path: Path | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: transparent;")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._build_ui()

    def _build_ui(self) -> None:
        self.modal = QFrame(self)
        self.modal.setObjectName("easyfindModal")
        self.modal.setStyleSheet(MODAL_STYLE)
        self.modal.setFixedWidth(MODAL_WIDTH)

        modal_layout = QVBoxLayout(self.modal)
        modal_layout.setContentsMargins(32, 28, 32, 28)
        modal_layout.setSpacing(12)
        modal_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetFixedSize)

        self.title_label = QLabel()
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet(TITLE_STYLE)
        self.title_label.setWordWrap(True)
        modal_layout.addWidget(self.title_label)

        self.body_label = QLabel()
        self.body_label.setAlignment(Qt.AlignCenter)
        self.body_label.setWordWrap(True)
        self.body_label.setStyleSheet(BODY_STYLE)
        self.body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        modal_layout.addWidget(self.body_label)

        self.warning_label = QLabel()
        self.warning_label.setAlignment(Qt.AlignCenter)
        self.warning_label.setWordWrap(True)
        self.warning_label.setStyleSheet(WARNING_STYLE)
        self.warning_label.hide()
        modal_layout.addWidget(self.warning_label)

        self.details_label = QLabel()
        self.details_label.setAlignment(Qt.AlignCenter)
        self.details_label.setWordWrap(True)
        self.details_label.setStyleSheet(DETAIL_STYLE)
        self.details_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.details_label.hide()
        modal_layout.addWidget(self.details_label)

        self.stage_label = QLabel()
        self.stage_label.setAlignment(Qt.AlignCenter)
        self.stage_label.setStyleSheet(STAGE_STYLE)
        self.stage_label.hide()
        modal_layout.addWidget(self.stage_label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.hide()
        modal_layout.addWidget(self.progress_bar)

        self.model_snapshot_host = QWidget(self.modal)
        snapshot_layout = QVBoxLayout(self.model_snapshot_host)
        snapshot_layout.setContentsMargins(0, 4, 0, 0)
        snapshot_layout.setSpacing(6)
        self.model_snapshot_label = QLabel("Rendering model thumbnails…")
        self.model_snapshot_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.model_snapshot_label.setStyleSheet(SNAPSHOT_LABEL_STYLE)
        snapshot_layout.addWidget(self.model_snapshot_label)
        self.model_snapshot_host.setFixedHeight(SNAPSHOT_SIZE + 28)
        self.model_snapshot_host.hide()
        modal_layout.addWidget(self.model_snapshot_host)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addStretch()

        self.validate_button = QPushButton("Validate Again")
        self.validate_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.validate_button.clicked.connect(self.validate_requested.emit)
        self.validate_button.hide()

        self.build_button = QPushButton("Build EasyFind")
        self.build_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.build_button.clicked.connect(self.build_requested.emit)
        self.build_button.hide()

        self.open_folder_button = QPushButton("Open Containing Folder")
        self.open_folder_button.setStyleSheet(CHROME_BUTTON_STYLE)
        self.open_folder_button.clicked.connect(self._open_containing_folder)
        self.open_folder_button.hide()

        button_row.addWidget(self.build_button)
        button_row.addWidget(self.validate_button)
        button_row.addWidget(self.open_folder_button)
        button_row.addStretch()
        modal_layout.addLayout(button_row)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_modal()

    def _position_modal(self) -> None:
        """Center the modal with safe margins so rounded corners stay visible."""
        if not hasattr(self, "modal"):
            return
        self.modal.adjustSize()
        width = self.modal.width()
        height = self.modal.sizeHint().height()
        margin = MODAL_MARGIN
        max_height = max(120, self.height() - margin * 2)
        height = min(height, max_height)
        x = max(margin, (self.width() - width) // 2)
        y = max(margin, (self.height() - height) // 2)
        if y + height > self.height() - margin:
            y = max(margin, self.height() - height - margin)
        self.modal.setGeometry(x, y, width, height)

    def _hide_action_buttons(self) -> None:
        self.build_button.hide()
        self.validate_button.hide()
        self.open_folder_button.hide()
        self.warning_label.hide()
        self.stage_label.hide()
        self.progress_bar.hide()
        self.details_label.hide()
        self.hide_model_snapshot_host()

    def show_model_snapshot_host(self) -> None:
        self.model_snapshot_host.show()
        self._position_modal()

    def hide_model_snapshot_host(self) -> None:
        self.model_snapshot_host.hide()
        self._position_modal()

    def model_snapshot_container(self) -> QWidget:
        return self.model_snapshot_host

    def _set_content(
        self,
        *,
        title: str,
        body: str,
        warning: str = "",
        details: str = "",
    ) -> None:
        self.title_label.setText(title)
        self.body_label.setText(body)
        if warning:
            self.warning_label.setText(warning)
            self.warning_label.show()
        else:
            self.warning_label.hide()
        if details:
            self.details_label.setText(details)
            self.details_label.show()
        else:
            self.details_label.hide()
        self.modal.show()
        self.show()
        self._position_modal()
        self.update()

    def show_no_assets(self) -> None:
        self._hide_action_buttons()
        self._current_path = None
        self._set_content(
            title="No ROM loaded",
            body=(
                "Open a ROM or session from the browser, "
                "then open EasyFind Map again."
            ),
        )

    def show_loading(self, *, stage: str = "") -> None:
        self._hide_action_buttons()
        self._set_content(
            title="Loading EasyFind…",
            body=stage or "Reading the index and preparing the canvas.",
        )
        if stage:
            self.stage_label.setText(stage)
            self.stage_label.show()
        else:
            self.stage_label.hide()
        self.progress_bar.hide()
        self._position_modal()

    def show_invalid(self, path: Path, validation: EasyFindValidationReport) -> None:
        self._hide_action_buttons()
        self._current_path = path
        errors = validation.errors[:4]
        extra = ""
        if len(validation.errors) > 4:
            extra = f"\n\n…and {len(validation.errors) - 4} more issue(s)."
        detail_lines = "\n".join(f"• {err}" for err in errors)
        self._set_content(
            title="EasyFind could not be loaded",
            body=(
                "The EasyFind file for this game exists, but validation failed.\n\n"
                f"{detail_lines}{extra}\n\n"
                "Use Validate Again to re-check, or Build EasyFind to replace the file."
            ),
        )
        self.validate_button.show()
        self.build_button.show()

    def show_build_required(self, target_path: Path) -> None:
        self._hide_action_buttons()
        self._current_path = None
        self._set_content(
            title="EasyFind not built yet",
            body=(
                "EasyFind is a searchable index of every asset in this game, "
                f"saved as easyfind/{target_path.name}.\n\n"
                "Click Build EasyFind in the toolbar to create it."
            ),
            warning=(
                "Use Build EasyFind to choose a full build, selected types only, "
                "or a quick catch-up for missing graphics."
            ),
        )
        self.build_button.show()

    def show_building(self, *, stage: str = "", percent: int = 0) -> None:
        self._hide_action_buttons()
        self._set_content(
            title="Building EasyFind…",
            body="Indexing assets and writing the .easyfind file.",
            warning="Please wait — this may take a while.",
        )
        if stage:
            self.stage_label.setText(stage)
            self.stage_label.show()
        self.progress_bar.setValue(percent)
        self.progress_bar.show()
        self._position_modal()

    def update_build_progress(self, stage: str, percent: int) -> None:
        self.stage_label.setText(stage)
        self.stage_label.show()
        self.progress_bar.setValue(percent)
        self.progress_bar.show()
        self._position_modal()

    def show_build_success(
        self,
        path: Path,
        quick_open: EasyFindQuickOpen,
        validation: EasyFindValidationReport,
    ) -> None:
        self._hide_action_buttons()
        self._current_path = path
        self._set_content(
            title="EasyFind ready",
            body="The index was built and validated successfully.",
            details=self._format_details(
                path=path,
                quick_open=quick_open,
                validation=validation,
                include_preview_blobs=False,
            ),
        )
        self.validate_button.show()
        self.open_folder_button.show()
        self._position_modal()

    def show_existing_file(
        self,
        path: Path,
        quick_open: EasyFindQuickOpen,
        validation: EasyFindValidationReport | None = None,
    ) -> None:
        self._hide_action_buttons()
        self._current_path = path
        self._set_content(
            title="EasyFind ready",
            body="An index for this game is already available.",
            details=self._format_details(
                path=path,
                quick_open=quick_open,
                validation=validation,
                include_preview_blobs=True,
            ),
        )
        self.validate_button.show()
        self.open_folder_button.show()
        self._position_modal()

    def show_validation_result(
        self,
        path: Path,
        quick_open: EasyFindQuickOpen,
        validation: EasyFindValidationReport,
    ) -> None:
        self._current_path = path
        title = "Validation OK" if validation.ok else "Validation failed"
        body = "The index file looks good." if validation.ok else "The index file has problems."
        self._set_content(
            title=title,
            body=body,
            details=self._format_details(
                path=path,
                quick_open=quick_open,
                validation=validation,
                include_preview_blobs=True,
            ),
        )
        self.validate_button.show()
        self.open_folder_button.show()
        self._position_modal()

    def show_error(self, message: str) -> None:
        self._hide_action_buttons()
        self._set_content(
            title="EasyFind error",
            body=message,
        )

    def _format_details(
        self,
        *,
        path: Path,
        quick_open: EasyFindQuickOpen,
        validation: EasyFindValidationReport | None,
        include_preview_blobs: bool,
    ) -> str:
        counts = quick_open.counts
        lines = [
            path.name,
            f"{counts.get('assets', 0):,} assets · {counts.get('nodes', 0):,} nodes",
        ]
        if include_preview_blobs and counts.get("preview_blobs", 0):
            lines.append(f"{counts.get('preview_blobs', 0):,} preview blobs")
        if validation is not None:
            lines.append(f"Validation: {'OK' if validation.ok else 'FAILED'}")
            if validation.errors:
                for err in validation.errors[:3]:
                    lines.append(f"• {err}")
                if len(validation.errors) > 3:
                    lines.append(f"• …and {len(validation.errors) - 3} more")
        return "\n".join(lines)

    def _open_containing_folder(self) -> None:
        if self._current_path is None:
            return
        folder = str(self._current_path.parent)
        if sys.platform == "darwin":
            subprocess.run(["open", folder], check=False)
        elif sys.platform == "win32":
            subprocess.run(["explorer", folder], check=False)
        else:
            subprocess.run(["xdg-open", folder], check=False)
