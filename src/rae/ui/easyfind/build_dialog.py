"""EasyFind build scope dialog."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...easyfind.build_options import (
    BUILD_MODE_CATCHUP,
    BUILD_MODE_FULL,
    BUILD_MODE_TYPES,
    EasyFindBuildOptions,
)
from ...easyfind.canvas_filters import SECTION_DISPLAY_NAMES, SECTION_ORDER
from ..constants import CHROME_BUTTON_STYLE

_BAKEABLE_KINDS = tuple(
    kind for kind in SECTION_ORDER if kind not in {"audio", "animation"}
)


class EasyFindBuildDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        has_existing_file: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Build EasyFind")
        self.setMinimumWidth(360)
        self._has_existing = has_existing_file
        self._type_checks: dict[str, QCheckBox] = {}
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        intro = QLabel(
            "Choose what to bake into the EasyFind index. "
            "Use partial updates to avoid rebuilding everything."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        self.mode_group = QButtonGroup(self)

        self.full_radio = QRadioButton("Full rebuild (replace entire index)")
        self.full_radio.setChecked(not self._has_existing)
        self.mode_group.addButton(self.full_radio)
        layout.addWidget(self.full_radio)

        self.types_radio = QRadioButton("Bake selected types only")
        self.types_radio.setChecked(self._has_existing)
        self.mode_group.addButton(self.types_radio)
        layout.addWidget(self.types_radio)

        self.catchup_radio = QRadioButton("Catch up missing graphics (new decoders / gaps)")
        self.catchup_radio.setEnabled(self._has_existing)
        self.mode_group.addButton(self.catchup_radio)
        layout.addWidget(self.catchup_radio)

        layout.addWidget(QLabel("Types to bake"))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMaximumHeight(180)
        host = QWidget()
        host_layout = QVBoxLayout(host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        for kind in _BAKEABLE_KINDS:
            cb = QCheckBox(SECTION_DISPLAY_NAMES.get(kind, kind))
            cb.setChecked(kind == "model")
            self._type_checks[kind] = cb
            host_layout.addWidget(cb)
        host_layout.addStretch()
        scroll.setWidget(host)
        layout.addWidget(scroll)

        hint = QLabel(
            "Full rebuild: new index + selected types (or all if none checked). "
            "Selected types: rebake only those previews and refresh lookup tables. "
            "Catch up: append previews for assets that still have none."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #b0b0b0; font-size: 11px;")
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        for button in buttons.buttons():
            button.setStyleSheet(CHROME_BUTTON_STYLE)
        layout.addWidget(buttons)

    def selected_options(self) -> EasyFindBuildOptions:
        kinds = frozenset(key for key, cb in self._type_checks.items() if cb.isChecked())
        if self.catchup_radio.isChecked():
            return EasyFindBuildOptions(mode=BUILD_MODE_CATCHUP)
        if self.types_radio.isChecked():
            return EasyFindBuildOptions(mode=BUILD_MODE_TYPES, bake_node_kinds=kinds)
        return EasyFindBuildOptions(mode=BUILD_MODE_FULL, bake_node_kinds=kinds)
