import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from chronocat_ground.ui.main_window_pages import VIEWS
from tests.test_flight_mapping import packet_with_adc


class PageWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)
        from chronocat_ground.main_window import MainWindow

        self.window = MainWindow(Path(self.directory.name) / "a.db")
        self.window.show()
        self.addCleanup(self.window.close)
        self.addCleanup(setattr, self.window, "_closing", True)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_page_moves_to_its_own_window_and_back(self) -> None:
        window = self.window
        heating_page = window.pages.widget(VIEWS.index("HEATING"))
        window.switch_view("DASHBOARD")
        window.pop_out_view("HEATING")
        popped = window.popped_out_views()["HEATING"]
        self.assertIs(popped.page, heating_page)
        self.assertTrue(popped.isVisible())
        self.assertIsNot(window.pages.widget(VIEWS.index("HEATING")), heating_page)
        self.assertEqual(window.pages.count(), len(VIEWS))
        self.assertEqual(window.pages.currentIndex(), VIEWS.index("DASHBOARD"))

        # It keeps updating where it is.
        window.on_telemetry_packet(packet_with_adc(), "test", time.monotonic(), time.time())
        self.assertIsNotNone(window.pid_page.readings[0])

        # Closing the window puts the page back at its own tab.
        popped.close()
        self.app.processEvents()
        self.assertNotIn("HEATING", window.popped_out_views())
        self.assertIs(window.pages.widget(VIEWS.index("HEATING")), heating_page)
        self.assertEqual(window.pages.currentIndex(), VIEWS.index("DASHBOARD"))

    def test_choosing_the_tab_brings_the_window_forward_without_resetting_it(self) -> None:
        from PySide6.QtCore import Qt

        window = self.window
        window.pop_out_view("HEATING")
        popped = window.popped_out_views()["HEATING"]
        popped.setWindowState(Qt.WindowMaximized)
        window.switch_view("HEATING")
        self.assertTrue(popped.windowState() & Qt.WindowMaximized)
        popped.setWindowState(Qt.WindowFullScreen)
        window.switch_view("HEATING")
        self.assertTrue(popped.windowState() & Qt.WindowFullScreen)
        # A minimized window is restored to whatever it was before.
        popped.setWindowState(Qt.WindowMaximized | Qt.WindowMinimized)
        window.switch_view("HEATING")
        self.assertFalse(popped.windowState() & Qt.WindowMinimized)
        self.assertTrue(popped.windowState() & Qt.WindowMaximized)
        self.assertIn("HEATING", window.popped_out_views())

    def test_the_tab_menu_offers_the_right_actions(self) -> None:
        window = self.window
        self.assertEqual([a.text() for a in window.tab_menu("HEATING").actions()], ["Open in new window"])
        window.tab_menu("HEATING").actions()[0].trigger()
        self.assertIn("HEATING", window.popped_out_views())
        self.assertEqual(
            [a.text() for a in window.tab_menu("HEATING").actions()],
            ["Show its window", "Bring back into the main window"],
        )
        window.tab_menu("HEATING").actions()[1].trigger()
        self.assertNotIn("HEATING", window.popped_out_views())

    def test_samples_refresh_in_their_own_window(self) -> None:
        window = self.window
        window.switch_view("DASHBOARD")
        window.pop_out_view("SAMPLES")
        window.on_telemetry_packet(packet_with_adc(), "test", time.monotonic(), time.time())
        card = window.sample_cards[1]
        self.assertIn("V", card.reading_label.text())
        self.assertTrue(window.view_is_visible("SAMPLES"))

    def test_choosing_a_popped_tab_keeps_the_placeholder_and_shutdown_closes_windows(self) -> None:
        window = self.window
        window.pop_out_view("BOARDS")
        window.switch_view("BOARDS")
        self.assertEqual(window.pages.currentIndex(), VIEWS.index("BOARDS"))
        popped = window.popped_out_views()["BOARDS"]
        window.close_page_windows()
        self.assertFalse(popped.isVisible())
        self.assertEqual(window.popped_out_views(), {})


if __name__ == "__main__":
    unittest.main()
