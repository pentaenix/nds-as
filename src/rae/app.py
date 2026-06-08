"""Desktop UI entrypoint."""
from __future__ import annotations

import os
import sys

from PySide6.QtWidgets import QApplication

from .ui import MainWindow


def main() -> None:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
