import math
import os
import struct
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from chronocat_ground.history_seed import load_seed_history
from chronocat_ground.main_window import MainWindow
from chronocat_ground.protocol import parse_telemetry_packet
from chronocat_ground.telemetry_db import TelemetryDb
from chronocat_ground.telemetry_history import TelemetryHistory
from tests.test_protocol import combined_telemetry_packet_v5


def record(path: Path, walls) -> None:
    data = bytearray(combined_telemetry_packet_v5())
    # All twelve ADC channels valid, each reading just above midscale.
    struct.pack_into(">H", data, 53, 0x0FFF)
    struct.pack_into(">12I", data, 55, *((0x800100 + slot) << 8 for slot in range(12)))
    decoded = parse_telemetry_packet(bytes(data))
    database = TelemetryDb(path)
    history = TelemetryHistory(database)
    for wall in walls:
        history.record(decoded.standard, 0.0, wall, decoded.pid)
    database.close()


class HistorySeedTest(unittest.TestCase):
    def test_recent_rows_come_back_on_the_session_clock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "seed.db"
            # Two hours ago (outside the window) and the last minute before a restart.
            record(path, [1000.0, 8000.0, 8001.0, 8002.0])
            database = TelemetryDb(path)
            seed = load_seed_history(database.conn, now_wall=8100.0, now_monotonic=50.0)
            database.close()
        self.assertEqual([point[0] for point in seed.geiger[1]], [-50.0, -49.0, -48.0])
        self.assertEqual(len(seed.adc[3]), 3)
        self.assertEqual(len(seed.packets), 3)
        monotonic, temperatures, duties = seed.packets[0]
        self.assertEqual(monotonic, -50.0)
        self.assertEqual(temperatures[15], 24.0)
        self.assertEqual(len(duties), 12)

    def test_a_restarted_window_shows_the_recent_trend(self) -> None:
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as directory:
            QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, directory)
            path = Path(directory) / "seed.db"
            now = time.time()
            record(path, [now - 120.0 + second for second in range(60)])
            window = MainWindow(path)
            try:
                history = window.telemetry_history
                self.assertEqual(len(history._geiger_points[0]), 60)
                newest = history._geiger_points[0][-1]
                # The newest stored point sits about a minute before now.
                self.assertAlmostEqual(time.monotonic() - newest[0], 61.0, delta=2.0)
                page = window.pid_page
                self.assertEqual(len(page.temperature_history[0]), 60)
                self.assertEqual(len(page.ambient_history[15]), 60)
                self.assertFalse(math.isnan(page.average_temperature_history[-1][1]))
                self.assertEqual(len(page.average_duty_history), 60)
                # Board off: no packet arrives, yet the other plots show the stored data.
                self.assertFalse(window.dashboard.geiger_plot._empty_label.isVisibleTo(window.dashboard.geiger_plot))
                self.assertFalse(window.radiation.rate_plot._empty_label.isVisibleTo(window.radiation.rate_plot))
                window.switch_view("SAMPLES")
                card = window.sample_cards[0]
                self.assertFalse(card.plot._empty_label.isVisibleTo(card.plot))
            finally:
                window._closing = True
                window.close()
                app.processEvents()


if __name__ == "__main__":
    unittest.main()


class EmptyPlotClickTest(unittest.TestCase):
    def test_the_no_data_label_lets_clicks_reach_the_plot(self) -> None:
        from PySide6.QtCore import Qt
        from chronocat_ground.plot_widget import PlotWidget

        QApplication.instance() or QApplication([])
        plot = PlotWidget("Dose rate", "No data")
        self.assertTrue(plot._empty_label.testAttribute(Qt.WA_TransparentForMouseEvents))
        plot.close()
