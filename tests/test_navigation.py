import tempfile
import unittest
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from chronocat_ground.main_window import MainWindow
from chronocat_ground.ui.main_window_pages import NAV_SIDE, NAV_TOP, VIEW_HEALTH


class NavigationStyleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        # Keep the test away from the user's real settings file.
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def window(self, name: str) -> MainWindow:
        window = MainWindow(Path(self.directory.name) / name)
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        return window

    def test_sidebar_fits_narrower_windows_and_is_remembered(self) -> None:
        window = self.window("a.db")
        window.show()
        self.app.processEvents()
        self.assertEqual(window.navigation_style, NAV_TOP)
        top_width = window.minimumSizeHint().width()

        window.set_navigation_style(NAV_SIDE)
        self.app.processEvents()
        self.assertTrue(window.side_nav.isVisibleTo(window))
        self.assertFalse(window.top_nav.isVisibleTo(window))
        self.assertLess(window.minimumSizeHint().width(), top_width - 300)

        window.switch_view(VIEW_HEALTH)
        self.assertEqual(
            sorted(button.objectName() for button in window.view_buttons[VIEW_HEALTH]),
            ["navSideActive", "navTabActive"],
        )
        self.assertEqual(self.window("b.db").navigation_style, NAV_SIDE)
