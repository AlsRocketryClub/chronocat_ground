from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sqlite3
import stat as stat_module
import sys
from typing import Literal

import PySide6
from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from .main_window import MainWindow
from .telemetry_db import DEFAULT_DATABASE_PATH, archive_database
from .ui.styles import APPLICATION_STYLE


def configure_qt_plugin_paths() -> Path:
    """Use the Qt plugins shipped with the active PySide6 installation."""
    plugin_path = Path(PySide6.__file__).resolve().parent / "Qt" / "plugins"
    platform_path = plugin_path / "platforms"
    if not platform_path.is_dir():
        raise RuntimeError(f"PySide6 platform plugins not found at {platform_path}")

    os.environ["QT_PLUGIN_PATH"] = str(plugin_path)
    if sys.platform == "darwin":
        # Qt's directory scanner skips plugins marked hidden by a .venv parent.
        for plugin in plugin_path.rglob("*.dylib"):
            flags = plugin.stat().st_flags
            if flags & stat_module.UF_HIDDEN:
                os.chflags(plugin, flags & ~stat_module.UF_HIDDEN)

    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(platform_path)
    QCoreApplication.setLibraryPaths([str(plugin_path)])
    return plugin_path


def choose_database_action(path: Path) -> Literal["continue", "new"] | None:
    """Ask whether an existing telemetry database should be continued."""
    stat = path.stat()
    size_mb = stat.st_size / 1_048_576
    modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")

    dialog = QMessageBox()
    dialog.setIcon(QMessageBox.Question)
    dialog.setWindowTitle("Telemetry Database")
    dialog.setText("Continue the existing telemetry database or start a new one?")
    dialog.setInformativeText(
        f"File: {path.name}\n"
        f"Size: {size_mb:.2f} MB\n"
        f"Last modified: {modified}\n\n"
        "Starting new will preserve this database in a timestamped archive."
    )
    continue_button = dialog.addButton("Continue Existing", QMessageBox.AcceptRole)
    new_button = dialog.addButton("Archive && Start New", QMessageBox.DestructiveRole)
    quit_button = dialog.addButton("Quit", QMessageBox.RejectRole)
    for button in (continue_button, new_button, quit_button):
        button.setMinimumWidth(max(110, button.sizeHint().width() + 16))
    dialog.setDefaultButton(continue_button)
    dialog.setEscapeButton(quit_button)
    dialog.exec()

    clicked = dialog.clickedButton()
    if clicked is continue_button:
        return "continue"
    if clicked is new_button:
        return "new"
    return None


def main() -> int:
    configure_qt_plugin_paths()
    app = QApplication(sys.argv)
    app.setStyleSheet(APPLICATION_STYLE)

    icon_path = os.path.join(os.path.dirname(__file__), "CHRONO-CAT_logo.png")
    icon = QIcon(icon_path)
    if not icon.isNull():
        app.setWindowIcon(icon)

    database_path = DEFAULT_DATABASE_PATH
    startup_message = f"Continuing database {database_path}"
    if database_path.exists():
        action = choose_database_action(database_path)
        if action is None:
            return 0
        if action == "new":
            try:
                archive = archive_database(database_path)
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                QMessageBox.critical(
                    None,
                    "Database Archive Failed",
                    f"The existing database was not replaced.\n\n{exc}",
                )
                return 1
            startup_message = f"Database archived to {archive}; new database created"
    else:
        startup_message = f"Created new database {database_path}"

    window = MainWindow(database_path)
    window.log(startup_message)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
