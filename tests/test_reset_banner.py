import os
import struct
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from chronocat_ground.main_window import MainWindow
from chronocat_ground.protocol import parse_telemetry_packet
from chronocat_ground.reset_monitor import (
    CAUSE_COMMAND,
    CAUSE_POWER,
    CAUSE_WATCHDOG,
    BoardReset,
    ResetMonitor,
    format_ago,
)
from chronocat_ground.ui.reset_banner import ResetBanner
from tests.test_protocol import combined_telemetry_packet_v5

WATCHDOG_FLAG = 1 << 4


class ResetMonitorTest(unittest.TestCase):
    def test_uptime_drop_is_a_restart_dated_from_the_new_uptime(self) -> None:
        monitor = ResetMonitor()
        self.assertIsNone(monitor.update(60_000, False, 1000.0))
        self.assertIsNone(monitor.update(61_000, False, 1001.0))
        reset = monitor.update(5_000, True, 1010.0, (0, 3))
        self.assertEqual(reset, BoardReset(CAUSE_WATCHDOG, 1005.0, 61_000, (0, 3)))
        self.assertEqual(monitor.update(3_000, False, 1020.0).cause, CAUSE_POWER)

    def test_an_old_watchdog_reset_shows_on_the_first_packet_only(self) -> None:
        monitor = ResetMonitor()
        reset = monitor.update(3_600_000, True, 10_000.0)
        self.assertEqual((reset.cause, reset.reset_wall, reset.previous_uptime_ms), (CAUSE_WATCHDOG, 6400.0, None))
        self.assertIsNone(monitor.update(3_601_000, True, 10_001.0))
        self.assertIsNone(ResetMonitor().update(3_600_000, False, 10_000.0))

    def test_a_reset_soon_after_the_command_is_commanded(self) -> None:
        monitor = ResetMonitor()
        monitor.update(100_000, False, 1000.0)
        monitor.note_reset_command(1001.0)
        self.assertEqual(monitor.update(4_000, True, 1006.0).cause, CAUSE_COMMAND)
        # The command explains one reset only.
        monitor.update(1_000, True, 1100.0)
        self.assertEqual(monitor.update(500, True, 1200.0).cause, CAUSE_WATCHDOG)

    def test_ago_text(self) -> None:
        self.assertEqual(
            [format_ago(s) for s in (3, 45, 720, 3600, 3900, 200_000)],
            ["just now", "45 s ago", "12 min ago", "1 h ago", "1 h 5 min ago", "2 d ago"],
        )


class ResetBannerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_text_counts_resets_and_closes_only_on_dismiss(self) -> None:
        now = datetime(2026, 10, 8, 14, 44, 7).timestamp()
        banner = ResetBanner()
        banner.add_reset(BoardReset(CAUSE_WATCHDOG, now - 720, 8_040_000, (0, 3, 7)))
        banner.refresh(now)
        self.assertEqual(banner.headline.text(), "Board reset by the watchdog at 14:32:07 (12 min ago)")
        self.assertEqual(
            banner.detail.text(),
            "It had been running for 2h 14m before that.  PID running on H0, H3, H7.",
        )
        self.assertEqual(banner.property("kind"), "warning")
        banner.add_reset(BoardReset(CAUSE_POWER, now - 60, 600_000, ()))
        banner.refresh(now)
        self.assertTrue(banner.headline.text().startswith("2 board resets since 14:32:07, latest at 14:43:07"))
        self.assertFalse(banner.isHidden())

        dismissed = []
        banner.dismissed.connect(dismissed.append)
        banner.dismiss()
        self.assertTrue(banner.isHidden())
        self.assertEqual(dismissed, [now - 60])

    def test_commanded_resets_are_grey(self) -> None:
        banner = ResetBanner()
        banner.add_reset(BoardReset(CAUSE_COMMAND, 0.0, 5_000, None))
        self.assertEqual(banner.property("kind"), "info")
        self.assertEqual(banner.detail.text(), "It had been running for 5s before that.")


def telemetry(uptime_ms: int, flags: int = 0x0007):
    data = bytearray(combined_telemetry_packet_v5())
    struct.pack_into(">H", data, 6, flags)
    struct.pack_into(">I", data, 10, uptime_ms)
    return parse_telemetry_packet(bytes(data))


class MainWindowResetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def window(self, name: str) -> MainWindow:
        window = MainWindow(Path(self.directory.name) / name)
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        return window

    def test_a_dismissed_reset_does_not_come_back_after_a_ground_restart(self) -> None:
        window = self.window("a.db")
        window.on_telemetry_packet(telemetry(60_000), "test", 1.0, 1000.0)
        self.assertTrue(window.reset_banner.isHidden())
        window.on_telemetry_packet(telemetry(2_000, 0x0007 | WATCHDOG_FLAG), "test", 2.0, 1010.0)
        self.assertFalse(window.reset_banner.isHidden())
        self.assertEqual(window.reset_banner.resets[0].cause, CAUSE_WATCHDOG)
        window.reset_banner.dismiss()

        # A new ground session sees the same reset through the still-set flag.
        later = self.window("b.db")
        later.on_telemetry_packet(telemetry(62_000, 0x0007 | WATCHDOG_FLAG), "test", 1.0, 1070.0)
        self.assertTrue(later.reset_banner.isHidden())


if __name__ == "__main__":
    unittest.main()


class DatabaseFailureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_stopped_writer_is_logged_once_and_shown_in_health(self) -> None:
        window = MainWindow(Path(self.directory.name) / "a.db")
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        window.adc_db._writer_error = RuntimeError("disk full")
        for second in range(3):
            window.on_telemetry_packet(telemetry(60_000 + second * 1000), "test", 1.0 + second, 1000.0 + second)
        log = window.log_view.toPlainText()
        self.assertEqual(log.count("Telemetry database logging failed"), 1)
        database = next(item for item in window._health_items if item.key == "database")
        self.assertEqual((database.state, database.value), ("error", "not recording"))


class ResetAcknowledgementTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_closing_the_banner_clears_the_watchdog_warning(self) -> None:
        window = MainWindow(Path(self.directory.name) / "a.db")
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        now = time.time()
        window.on_telemetry_packet(telemetry(60_000, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), now)
        reset = next(item for item in window._health_items if item.key == "reset")
        self.assertEqual(reset.state, "warning")
        window.reset_banner.dismiss()
        reset = next(item for item in window._health_items if item.key == "reset")
        self.assertEqual((reset.state, reset.value), ("ok", "watchdog (seen)"))
        # A later packet from the same board run stays acknowledged.
        window.on_telemetry_packet(telemetry(61_000, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), now + 1)
        reset = next(item for item in window._health_items if item.key == "reset")
        self.assertEqual(reset.state, "ok")


class ResetDriftTest(unittest.TestCase):
    """The board clock runs ~1.4 % fast, so 'now minus uptime' drifts during a run."""

    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def window(self, name):
        window = MainWindow(Path(self.directory.name) / name)
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        return window

    @staticmethod
    def reset_item(window):
        return next(item for item in window._health_items if item.key == "reset")

    def test_acknowledgement_survives_hours_of_drift_and_a_ground_restart(self) -> None:
        start = 100_000.0
        fast = 1.0138
        window = self.window("a.db")
        window.on_telemetry_packet(telemetry(60_000, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), start)
        window.reset_banner.dismiss()
        # Three hours later the board's uptime has run 1.38 % ahead of the wall clock.
        later = start + 3 * 3600
        uptime = int((60 + 3 * 3600 * fast) * 1000)
        window.on_telemetry_packet(telemetry(uptime, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), later)
        self.assertEqual(self.reset_item(window).state, "ok")

        # A ground restart then: the banner stays closed and Health stays quiet.
        restarted = self.window("b.db")
        restarted.on_telemetry_packet(telemetry(uptime, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), later)
        self.assertTrue(restarted.reset_banner.isHidden())
        self.assertEqual(self.reset_item(restarted).state, "ok")

    def test_a_new_reset_after_acknowledging_warns_again(self) -> None:
        window = self.window("a.db")
        window.on_telemetry_packet(telemetry(60_000, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), 100_000.0)
        window.reset_banner.dismiss()
        window.on_telemetry_packet(telemetry(3_000, 0x0007 | WATCHDOG_FLAG), "test", time.monotonic(), 100_600.0)
        self.assertEqual(self.reset_item(window).state, "warning")
        self.assertFalse(window.reset_banner.isHidden())
