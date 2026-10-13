"""The flight sample mapping (adc_mapping.txt) on the Samples, Boards and Heating pages."""

import os
import struct
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from chronocat_ground.protocol import parse_telemetry_packet
from chronocat_ground.sample_layout import HEATERS_WITH_SAMPLES, SIGNAL_SLOTS
from chronocat_ground.ui.board_map import BoardMapWidget
from tests.test_protocol import combined_telemetry_packet_v5


def packet_with_adc():
    data = bytearray(combined_telemetry_packet_v5())
    struct.pack_into(">H", data, 53, 0x0FFF)
    struct.pack_into(">12I", data, 55, *(((0x800000 + 400 * (slot + 1)) << 8) for slot in range(12)))
    return parse_telemetry_packet(bytes(data))


class FlightMappingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        self.directory = tempfile.TemporaryDirectory()
        QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def window(self, name="a.db"):
        from chronocat_ground.main_window import MainWindow

        window = MainWindow(Path(self.directory.name) / name)
        self.addCleanup(window.close)
        self.addCleanup(setattr, window, "_closing", True)
        return window

    def test_heaters_under_samples_skip_the_u5_and_u2_spots(self) -> None:
        self.assertEqual(HEATERS_WITH_SAMPLES, (0, 1, 3, 4, 6, 7, 9, 10))

    def test_signals_only_hides_seven_channels_and_is_remembered(self) -> None:
        window = self.window()
        window.samples_signals_only_button.setChecked(True)
        visible = [card.slot for card in window.sample_cards if not card.isHidden()]
        self.assertEqual(tuple(visible), SIGNAL_SLOTS)
        self.assertTrue(window.sample_cards[0].role_label.text() == "NOISE")
        hidden_labels = [slot for slot, (label, _) in window.dashboard.sample_values.items() if label.isHidden()]
        self.assertEqual(sorted(hidden_labels), sorted(set(range(12)) - set(SIGNAL_SLOTS)))
        later = self.window("b.db")
        self.assertTrue(later.samples_signals_only_button.isChecked())
        self.assertTrue(later.sample_cards[10].isHidden())

    def test_enable_pid_offers_the_eight_or_all_and_sends_only_the_choice(self) -> None:
        window = self.window()
        page = window.pid_page
        page._mapped_mask = 0x0FFF

        def choose(label_start):
            def fake_exec(box):
                fake_exec.button = next(b for b in box.buttons() if b.text().startswith(label_start))
                return 0
            return fake_exec

        for label, expected in (("Heaters with samples", list(HEATERS_WITH_SAMPLES)), ("All heaters", list(range(12)))):
            fake = choose(label)
            with mock.patch.object(QMessageBox, "exec", fake), \
                    mock.patch.object(QMessageBox, "clickedButton", lambda box: fake.button):
                self.assertEqual(page.choose_pid_heaters(), expected)

        sent = []
        window.client._socket = object()  # looks connected; nothing goes on the wire
        with mock.patch.object(window, "send_command", lambda command, heater, value, *a, **k: sent.append(heater)):
            window.set_all_pid(True, 20.0, page.profile_combo.currentText(), list(HEATERS_WITH_SAMPLES))
        window.client._socket = None
        self.assertEqual(window.pending_pid_operation.heater_ids, HEATERS_WITH_SAMPLES)
        self.assertEqual(sent, [0])  # the sequence starts with H0 and continues on each reply


class BoardMapReadingTest(unittest.TestCase):
    def test_signal_readings_are_shown_and_open_their_channel(self) -> None:
        app = QApplication.instance() or QApplication([])
        widget = BoardMapWidget()
        widget.resize(1300, widget.heightForWidth(1300))
        widget.set_readings(packet_with_adc().standard)
        widget.grab()  # paint, which records where each reading sits
        slots = [slot for _rect, slot in widget._reading_rects]
        self.assertEqual(sorted(slots), list(SIGNAL_SLOTS))
        requested = []
        widget.channel_requested.connect(requested.append)
        rect, slot = widget._reading_rects[0]
        self.assertEqual(widget._slot_at(rect.center()), slot)
        self.assertIsNone(widget._slot_at(QPointF(2, 2)))
        widget.channel_requested.emit(widget._slot_at(rect.center()))
        self.assertEqual(requested, [slot])
        widget.close()
        app.processEvents()


if __name__ == "__main__":
    unittest.main()
