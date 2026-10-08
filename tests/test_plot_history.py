import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest

import numpy as np
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QLabel, QWidget

from chronocat_ground.plot_history import HISTORY_BATCH_SIZE, PlotHistoryLoader, read_history_batch
from chronocat_ground.plot_widget import SERIES_COLORS, HistoryPlotWidget
from chronocat_ground.protocol import AD7177_BIPOLAR_MIDSCALE
from chronocat_ground.telemetry_db import TelemetryDb
from chronocat_ground.ui.main_window_pages import MainWindowPagesMixin


class DialogHost(MainWindowPagesMixin, QWidget):
    def __init__(self, path: Path) -> None:
        super().__init__()
        self.database_path = path
        self.plot_dialogs = {}
        self.plot_dialog_refs = {}
        self.samples_display_mode = "raw"
        self.sample_cards = [SimpleNamespace(
            toggle_button=QLabel("Sample"), reading_label=QLabel("123 Raw24"),
            status_label=QLabel("Status: 0x00 (ok)"),
        )]
        # Pop-out updates must not access the 300-point dashboard history.
        self.telemetry_history = None

    def log(self, message: str) -> None:
        raise AssertionError(message)


class PlotHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def wait_until(self, predicate, timeout=10.0) -> None:
        end = time.monotonic() + timeout
        while not predicate() and time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertTrue(predicate(), "background history loading did not finish")

    def test_ten_hour_popout_keeps_full_history_through_live_updates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            db = TelemetryDb(path, async_writes=True)
            host = DialogHost(path)
            count = 36_000
            wall = 1_700_000_000.0
            db.conn.executemany(
                "INSERT INTO geiger (ts_ms, received_wall, counter_id, dose_rate_cps, total_dose_sv, "
                "dose_time_sec, hv_voltage, stat_error_percent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(i * 1000, wall + i, 0, float(i), 0.0, i, 400, 0.0)
                 for i in range(count)],
            )
            db.conn.commit()
            try:
                host.show_geiger_dialog(0)
                refs = host.plot_dialog_refs["geiger_0"]
                plot = refs["plot"]
                loader = refs["loaders"][0]
                self.wait_until(lambda: plot.sample_count == count)
                self.assertEqual(plot._curve.xData[0], 0.0)
                self.assertEqual(plot._curve.xData[-1], count - 1)
                self.assertTrue(plot._curve.opts["autoDownsample"])
                self.assertTrue(plot._curve.opts["clipToView"])
                host.update_plot_dialogs()
                self.assertEqual(plot.sample_count, count)

                # Inspect old data while new data (with reset device time) arrives.
                plot.setXRange(100.0, 200.0, padding=0)
                plot.setYRange(50.0, 250.0, padding=0)
                old_range = plot.viewRange()
                for i in range(3):
                    db.insert_packet([], [(0, wall + count + i, 0,
                        float(count + i), 0.0, 0, 400, 0.0)], [])
                self.wait_until(lambda: plot.sample_count == count + 3)
                np.testing.assert_allclose(plot.viewRange(), old_range)
                self.assertEqual(plot._curve.yData[0], 0.0)
                self.assertEqual(plot._curve.yData[-1], count + 2)
                host.update_plot_dialogs()
                self.assertEqual(plot.sample_count, count + 3)
                # Repeated polling must not duplicate already loaded rows.
                self.wait_until(lambda: loader._future is None)
                self.assertEqual(plot.sample_count, count + 3)
                host.plot_dialogs["geiger_0"].close()
                self.assertTrue(loader._closed)
                self.assertFalse(host.plot_dialog_refs)
                self.assertFalse(host.plot_dialogs)
            finally:
                for dialog in list(host.plot_dialogs.values()):
                    dialog.close()
                host.close()
                db.close()
                self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_batches_are_incremental_and_raw_mode_stays_consistent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            db = TelemetryDb(path)
            try:
                db.conn.executemany(
                    "INSERT INTO adc (ts_ms, slot, raw24, received_wall) VALUES (?, ?, ?, ?)",
                    [(i * 1000, 0, AD7177_BIPOLAR_MIDSCALE + i, float(i))
                     for i in range(HISTORY_BATCH_SIZE + 5)],
                )
                db.conn.commit()
                cursor, points, more = read_history_batch(path, "adc", 0, 0, True)
                self.assertTrue(more)
                self.assertEqual(len(points), HISTORY_BATCH_SIZE)
                cursor2, points2, more = read_history_batch(path, "adc", 0, cursor, True)
                self.assertFalse(more)
                self.assertEqual(len(points2), 5)
                self.assertEqual(points2[0, 1], AD7177_BIPOLAR_MIDSCALE + HISTORY_BATCH_SIZE)
                cursor3, empty, more = read_history_batch(path, "adc", 0, cursor2, True)
                self.assertEqual(cursor3, cursor2)
                self.assertEqual(len(empty), 0)
                _, volts, _ = read_history_batch(path, "adc", 0, 0)
                self.assertEqual(volts[0, 1], 0.0)

                host = DialogHost(path)
                host.show_adc_graph_dialog(0)
                plot = host.plot_dialog_refs["adc_0"]["plot"]
                self.wait_until(lambda: plot.sample_count == HISTORY_BATCH_SIZE + 5)
                host.samples_display_mode = "voltage"
                host.update_plot_dialogs()
                self.assertEqual(plot.y_label, "Raw24")
                self.assertEqual(plot._curve.yData[0], AD7177_BIPOLAR_MIDSCALE)
                host.plot_dialogs["adc_0"].close()
                host.close()
            finally:
                db.close()
                self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_gaps_at_batch_boundaries_and_capacity_growth_preserve_samples(self) -> None:
        plot = HistoryPlotWidget("CPS")
        try:
            plot.append_history(np.array([[10.0, 1.0], [11.0, 2.0]]))
            plot.append_history(np.array([[20.0, 3.0], [21.0, 4.0]]))
            self.assertEqual(plot.sample_count, 4)
            self.assertTrue(np.isnan(plot._curve.yData[2]))
            large = np.column_stack((np.arange(22.0, 36_022.0), np.ones(36_000)))
            plot.append_history(large)
            self.assertEqual(plot.sample_count, 36_004)
            self.assertEqual(plot._curve.yData[0], 1.0)
            self.assertEqual(plot._curve.xData[-1], 36_011.0)
        finally:
            plot.close()

    def test_dashboard_material_plot_pops_out_all_six_channels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            db = TelemetryDb(path)
            host = DialogHost(path)
            wall = 1_700_000_000.0
            count = 500
            # TIPs-pentacene is ADC2/ADC3, slots 6-11.
            db.conn.executemany(
                "INSERT INTO adc (ts_ms, slot, raw24, received_wall) VALUES (?, ?, ?, ?)",
                [(i * 1000, slot, AD7177_BIPOLAR_MIDSCALE + slot * 1000, wall + i)
                 for i in range(count) for slot in range(6, 12)],
            )
            db.conn.commit()
            panel = host.build_dashboard_page()
            try:
                host.dashboard.sample_plots[1][0].on_double_click()
                plot = host.plot_dialog_refs["samples_1"]["plot"]
                self.wait_until(lambda: plot.sample_count == count * 6)
                self.assertEqual(len(plot._curves), 6)
                self.assertEqual(plot._history_names[0], "1a (ADC2 CH0)")
                self.assertEqual(plot._history_names[5], "3b (ADC3 CH2)")
                # Line colours match the Dashboard swatches.
                self.assertEqual(
                    [curve.opts["pen"].color().name() for curve in plot._curves],
                    list(SERIES_COLORS),
                )
            finally:
                for dialog in list(host.plot_dialogs.values()):
                    dialog.close()
                panel.close()
                host.close()
                db.close()
                self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_combined_geiger_click_loads_both_counters_on_shared_time_axis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            db = TelemetryDb(path)
            host = DialogHost(path)
            wall = 1_700_000_000.0
            count = 6000
            db.conn.executemany(
                "INSERT INTO geiger (ts_ms, received_wall, counter_id, dose_rate_cps, total_dose_sv, "
                "dose_time_sec, hv_voltage, stat_error_percent) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(i * 1000, wall + i + counter * 10, counter,
                  float(i + counter * 100), 0.0, i, 400, 0.0)
                 for i in range(count) for counter in (0, 1)],
            )
            db.conn.commit()
            panel = host.build_dashboard_page()
            try:
                host.dashboard.geiger_plot.on_double_click()
                self.assertNotIn("geiger_0", host.plot_dialogs)
                refs = host.plot_dialog_refs["geiger_all"]
                plot = refs["plot"]
                self.wait_until(lambda: plot.sample_count == count * 2)
                self.assertEqual(len(plot._curves), 2)
                self.assertEqual(plot._history_names, ("Geiger 1", "Geiger 2"))
                self.assertEqual(len(plot._legend.items), 2)
                for counter, curve in enumerate(plot._curves):
                    self.assertEqual(len(curve.yData), count)
                    self.assertEqual(curve.yData[0], counter * 100)
                    self.assertEqual(curve.xData[0] + plot._origin, wall + counter * 10)
                db.insert_geiger(0, 1, 9000.0, 0.0, 0, 400, 0.0,
                                 wall + count + 10)
                self.wait_until(lambda: plot.sample_count == count * 2 + 1)
                host.update_plot_dialogs()
                self.assertEqual(len(plot._curves[0].yData), count)
                self.assertEqual(plot._curves[1].yData[-1], 9000.0)
                loaders = refs["loaders"]
                host.plot_dialogs["geiger_all"].close()
                self.assertTrue(all(loader._closed for loader in loaders))
            finally:
                for dialog in list(host.plot_dialogs.values()):
                    dialog.close()
                panel.close()
                host.close()
                db.close()
                self.app.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_closing_during_background_load_stops_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.db"
            db = TelemetryDb(path)
            loader = PlotHistoryLoader(path, "geiger", 1)
            batches = []
            loader.batch_ready.connect(batches.append)
            loader.start()
            loader._poll()
            loader.close()
            QCoreApplication.processEvents()
            self.assertFalse(loader._timer.isActive())
            self.assertEqual(batches, [])
            db.close()


if __name__ == "__main__":
    unittest.main()


class HeatingHistoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_heating_series_and_averages_come_from_the_database(self) -> None:
        from chronocat_ground.protocol import parse_telemetry_packet
        from chronocat_ground.telemetry_history import TelemetryHistory
        from tests.test_protocol import combined_telemetry_packet_v5

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heating.db"
            db = TelemetryDb(path)
            history = TelemetryHistory(db)
            decoded = parse_telemetry_packet(combined_telemetry_packet_v5())
            for second in range(3):
                history.record(decoded.standard, float(second), 1000.0 + second, decoded.pid)
            db.close()

            _rowid, ambient, _more = read_history_batch(path, "temperature", 15, 0)
            self.assertEqual(ambient[:, 1].tolist(), [24.0] * 3)
            _rowid, duty, _more = read_history_batch(path, "duty", 2, 0)
            self.assertEqual(duty[:, 0].tolist(), [1000.0, 1001.0, 1002.0])
            self.assertAlmostEqual(duty[0, 1], decoded.pid.heaters[2].duty_permille / 10.0)

            expected_temperature = np.mean([
                decoded.standard.temperature_c(slot) for slot in range(12)
                if decoded.standard.temperature_c(slot) is not None
            ])
            last, average, _more = read_history_batch(path, "temperature_avg", 0, 0)
            self.assertEqual(len(average), 3)
            self.assertAlmostEqual(average[0, 1], expected_temperature)
            # Incremental reads continue after the last packet only.
            self.assertEqual(len(read_history_batch(path, "temperature_avg", 0, last)[1]), 0)
            _rowid, duty_average, _more = read_history_batch(path, "duty_avg", 0, 0)
            self.assertAlmostEqual(
                duty_average[0, 1],
                np.mean([heater.duty_permille for heater in decoded.pid.heaters]) / 10.0,
            )

    def test_double_clicking_a_heating_plot_opens_its_history(self) -> None:
        from chronocat_ground.pid_page import PidPage

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "heating.db"
            TelemetryDb(path).close()
            host = DialogHost(path)
            host.pid_page = PidPage()
            host.pid_page.plot_popout_requested.connect(host.show_heating_dialog)
            host.pid_page._select_ambient(15)
            host.pid_page.temperature_plot.on_double_click()
            host.pid_page._select_heater(4)
            host.pid_page.output_plot.on_double_click()
            host.pid_page.average_temperature_plot.on_double_click()
            host.pid_page.average_duty_plot.on_double_click()
            self.assertEqual(
                sorted(host.plot_dialogs),
                ["duty_4", "duty_avg", "temperature_15", "temperature_avg"],
            )
            self.assertEqual(host.plot_dialogs["temperature_15"].windowTitle(), "F2_U6 (ambient) TEMPERATURE")
            for dialog in list(host.plot_dialogs.values()):
                dialog.close()
            host.pid_page.close()
            host.close()
            self.app.sendPostedEvents(None, QEvent.DeferredDelete)
