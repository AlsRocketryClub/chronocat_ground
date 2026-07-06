from __future__ import annotations

import os
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from .main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)

    icon_path = os.path.join(os.path.dirname(__file__), "CHRONO-CAT_logo.png")
    icon = QIcon(icon_path)
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
