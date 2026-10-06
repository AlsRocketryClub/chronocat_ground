import unittest

from PySide6.QtWidgets import QApplication

from chronocat_ground.protocol_constants import TEMP_SENSOR_LABELS
from chronocat_ground.ui.board_map import BOARDS, BoardMapWidget


def labels(ids) -> list[str]:
    return [TEMP_SENSOR_LABELS[index] for index in ids]


class BoardMapTest(unittest.TestCase):
    def test_boards_follow_material_adc_and_sensor_bindings(self) -> None:
        first, second = BOARDS
        self.assertEqual((first.material, first.bus, first.adc_text, first.heater_text),
                         ("diF-TES-ADT", "I2C4", "ADC0 + ADC1", "H0–H5"))
        self.assertEqual((second.material, second.bus, second.adc_text, second.heater_text),
                         ("TIPs-pentacene", "I2C1", "ADC2 + ADC3", "H6–H11"))
        self.assertEqual(sorted(labels(first.sensor_ids)), ["U0", "U1", "U2", "U3", "U4", "U5", "U6", "U7"])
        self.assertTrue(all(label.startswith("F2_") for label in labels(second.sensor_ids)))

    def test_pairs_put_left_and_right_sensors_on_their_adc_channels(self) -> None:
        pairs = {
            pair.number: (
                labels((pair.left_sensor, pair.right_sensor)),
                pair.device_a.location,
                pair.device_b.location,
            )
            for pair in BOARDS[0].pairs
        }
        self.assertEqual(pairs, {
            1: (["U4", "U3"], "ADC0 CH0", "ADC0 CH1"),
            2: (["U0", "U1"], "ADC0 CH2", "ADC1 CH0"),
            3: (["U5", "U2"], "ADC1 CH1", "ADC1 CH2"),
        })

    def test_widget_paints_without_telemetry(self) -> None:
        app = QApplication.instance() or QApplication([])
        widget = BoardMapWidget()
        widget.resize(900, 700)
        self.assertFalse(widget.grab().isNull())
        app.processEvents()
