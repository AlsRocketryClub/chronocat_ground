import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from chronocat_ground import display_scale as scale
from chronocat_ground.display_scale import ScreenInfo

LAPTOP = ScreenInfo("Built-in Retina Display", 1470, 290.6)
BIG_4K = ScreenInfo("DELL U2720Q", 3840, 597.0)  # 27" at native resolution
OFFICE = ScreenInfo("HP 24", 1920, 531.0)  # 24" 1080p, coarser than the laptop


class DisplayScaleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)
        self.store = scale.settings()

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_automatic_size_matches_the_laptop_and_never_shrinks(self) -> None:
        self.assertEqual(scale.auto_percent(LAPTOP), 100)
        self.assertEqual(scale.auto_percent(BIG_4K), 130)
        self.assertEqual(scale.auto_percent(OFFICE), 100)
        self.assertEqual(scale.auto_percent(ScreenInfo("broken", 1920, 0.0)), 100)
        self.assertEqual(scale.auto_percent(ScreenInfo("nonsense", 1920, 2.0)), 100)

    def test_each_screen_keeps_its_own_size(self) -> None:
        self.assertTrue(scale.auto_enabled(self.store))  # on by default
        scale.set_stored_percent(self.store, LAPTOP.name, 110)
        self.assertEqual(scale.effective_percent(self.store, LAPTOP), 110)
        self.assertEqual(scale.effective_percent(self.store, BIG_4K), 130)
        self.assertEqual(
            scale.scale_factors_env(self.store, [LAPTOP, BIG_4K, OFFICE]),
            "Built-in Retina Display=1.1;DELL U2720Q=1.3",
        )
        scale.set_auto_enabled(self.store, False)
        self.assertEqual(scale.effective_percent(self.store, BIG_4K), 100)
        self.assertEqual(scale.effective_percent(self.store, LAPTOP), 110)
        scale.set_stored_percent(self.store, LAPTOP.name, None)
        self.assertIsNone(scale.scale_factors_env(self.store, [LAPTOP, BIG_4K]))

    def test_sizes_are_clamped_and_odd_names_are_left_alone(self) -> None:
        scale.set_stored_percent(self.store, LAPTOP.name, 500)
        self.assertEqual(scale.stored_percent(self.store, LAPTOP.name), 200)
        scale.set_stored_percent(self.store, LAPTOP.name, 12)
        self.assertEqual(scale.stored_percent(self.store, LAPTOP.name), 75)
        self.store.setValue("display/scale/Built-in Retina Display", "garbage")
        self.assertIsNone(scale.stored_percent(self.store, LAPTOP.name))
        odd = ScreenInfo("A;B=C", 3840, 597.0)
        self.assertIsNone(scale.scale_factors_env(self.store, [odd]))

    def test_startup_respects_a_manual_factor_and_skips_the_probe_when_unused(self) -> None:
        with mock.patch.dict(os.environ, {"QT_SCALE_FACTOR": "1.5"}):
            self.assertIsNone(scale.apply_display_scaling())
        scale.set_auto_enabled(self.store, False)
        with mock.patch.object(scale, "probe_screens", side_effect=AssertionError("probed")):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("QT_SCREEN_SCALE_FACTORS", None)
                os.environ.pop("QT_SCALE_FACTOR", None)
                self.assertIsNone(scale.apply_display_scaling())
        scale.set_auto_enabled(self.store, True)
        with mock.patch.object(scale, "probe_screens", return_value=[BIG_4K]):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("QT_SCREEN_SCALE_FACTORS", None)
                os.environ.pop("QT_SCALE_FACTOR", None)
                self.assertEqual(scale.apply_display_scaling(), "DELL U2720Q=1.3")
                self.assertEqual(os.environ["QT_SCREEN_SCALE_FACTORS"], "DELL U2720Q=1.3")


class DisplaySettingsPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def window(self):
        from chronocat_ground.main_window import MainWindow

        window = MainWindow(Path(self.directory.name) / "a.db")
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        return window

    def test_slider_reset_and_checkbox_save_per_screen(self) -> None:
        window = self.window()
        # The offscreen test display has no name; stand in for a real monitor.
        name = BIG_4K.name
        window._current_screen_info = lambda: BIG_4K
        window.switch_view("SETTINGS")
        self.assertTrue(window.scale_auto_checkbox.isChecked())
        self.assertFalse(window.scale_reset_button.isEnabled())
        window.scale_slider.setValue(142)
        self.assertEqual(scale.stored_percent(scale.settings(), name), 140)
        self.assertEqual(window.scale_value_label.text(), "140 %")
        self.assertIn("140 % after a restart", window.scale_note.text())
        self.assertTrue(window.scale_reset_button.isEnabled())
        window.scale_reset_button.click()
        self.assertIsNone(scale.stored_percent(scale.settings(), name))
        window.scale_auto_checkbox.setChecked(False)
        self.assertFalse(scale.auto_enabled(scale.settings()))
        self.assertEqual(window.scale_reset_button.text(), "Reset to 100 %")

    def test_an_unnamed_screen_cannot_be_resized(self) -> None:
        window = self.window()
        window._current_screen_info = lambda: None
        window.switch_view("SETTINGS")
        self.assertFalse(window.scale_slider.isEnabled())
        self.assertIn("stays at 100 %", window.scale_note.text())

    def test_restart_relaunches_with_the_same_database(self) -> None:
        window = self.window()
        with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.Yes), \
                mock.patch("chronocat_ground.ui.main_window_pages.QProcess.startDetached",
                           return_value=(True, 1234)) as start, \
                mock.patch("chronocat_ground.ui.main_window_pages.QApplication.quit") as quit_app:
            window._restart_ground_station()
        program, arguments, directory = start.call_args.args
        self.assertEqual(arguments, ["-m", "chronocat_ground.main", "--continue-database"])
        self.assertEqual(directory, os.getcwd())
        quit_app.assert_called_once()


if __name__ == "__main__":
    unittest.main()
