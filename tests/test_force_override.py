import os
import struct
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox

from chronocat_ground.pid_page import PidPage
from chronocat_ground.protocol import parse_telemetry_packet
from tests.test_protocol import combined_telemetry_packet_v5

HEATER_RECORDS = 18 + 1 + 2 + 32 + 2 + 48 + 68 + 4


def pid_packet(temperature_mask=0xFFFF, result=0):
    data = bytearray(combined_telemetry_packet_v5())
    struct.pack_into(">H", data, 19, temperature_mask)
    data[HEATER_RECORDS + 6] = result
    return parse_telemetry_packet(bytes(data)).pid


class ForceOverrideTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.page = PidPage()
        self.forced: list[tuple[int, int]] = []
        self.page.force_duty_requested.connect(lambda heater, duty: self.forced.append((heater, duty)))

    def test_reasons_name_the_dead_sensor_and_the_latch(self) -> None:
        self.page.update_packet(pid_packet(temperature_mask=0xFFFF & ~(1 << 3), result=7), 0.0)
        reasons = self.page.manual_refusal_reasons(0)
        self.assertIn("sensor F1_U0 has no valid reading", reasons)
        self.assertTrue(any("overtemperature latch" in reason for reason in reasons))

    def test_ambient_sensors_are_listed_without_a_heater(self) -> None:
        data = bytearray(combined_telemetry_packet_v5())
        struct.pack_into(">H", data, 19, 0x0FFF | (1 << 15))
        self.page.update_ambient(parse_telemetry_packet(bytes(data)).standard, 1.0)
        rows = {row.sensor_id: row for row in self.page.ambient_rows}
        self.assertEqual(len(rows), 4)
        self.assertEqual(rows[15].state_label.text(), "AMBIENT")
        self.assertTrue(rows[15].temperature_label.text().endswith(" C"))
        self.assertEqual(rows[12].state_label.text(), "NO READING")

    def test_clicking_an_ambient_sensor_shows_its_plot_without_heater_controls(self) -> None:
        data = bytearray(combined_telemetry_packet_v5())
        struct.pack_into(">H", data, 19, 0x0FFF | (1 << 15))
        standard = parse_telemetry_packet(bytes(data)).standard
        self.page.update_ambient(standard, 1.0)
        self.page.update_ambient(standard, 2.0)
        self.page.show()
        rows = {row.sensor_id: row for row in self.page.ambient_rows}
        rows[15].clicked.emit(15)
        self.assertEqual(self.page.detail_title.text(), "F2_U6")
        self.assertEqual(self.page.detail_status.text(), "AMBIENT")
        self.assertEqual(len(self.page.ambient_history[15]), 2)
        self.assertFalse(self.page.controls_panel.isVisible())
        self.assertFalse(self.page.output_plot.isVisible())
        self.assertTrue(rows[15].property("selected"))
        self.assertFalse(self.page.rows[0].property("selected"))

        self.page.rows[2].clicked.emit(2)
        self.assertEqual(self.page.detail_title.text(), "H2")
        self.assertTrue(self.page.controls_panel.isVisible())
        self.assertFalse(rows[15].property("selected"))

    def test_force_is_sent_only_when_confirmed(self) -> None:
        self.page.update_packet(pid_packet(), 0.0)
        for choice, expected in (("cancel", []), ("force", [(0, 150)])):
            clicked = {}

            def fake_exec(box, choice=choice):
                self.assertIs(box.defaultButton(), box.button(QMessageBox.Cancel))
                buttons = {button.text(): button for button in box.buttons()}
                clicked["button"] = (
                    buttons["Force anyway"] if choice == "force" else box.button(QMessageBox.Cancel)
                )
                return 0

            with mock.patch.object(QMessageBox, "exec", fake_exec), mock.patch.object(
                QMessageBox, "clickedButton", lambda box: clicked["button"]
            ):
                self.forced.clear()
                self.page.offer_force_override(0, 150)
            self.assertEqual(self.forced, expected)


if __name__ == "__main__":
    unittest.main()
