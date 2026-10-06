import unittest

from chronocat_ground.protocol_constants import TEMP_SENSOR_LABELS
from chronocat_ground.sample_layout import SAMPLE_CHANNELS, SAMPLE_COLUMNS


def describe(slot: int) -> tuple[str, str, tuple[str, str]]:
    channel = SAMPLE_CHANNELS[slot]
    sensors = tuple(TEMP_SENSOR_LABELS[index] for index in channel.temperature_sensor_ids)
    return channel.name, channel.location, sensors


class SampleLayoutTest(unittest.TestCase):
    def test_first_board_is_dif_tes_adt_with_board_one_sensors(self) -> None:
        self.assertEqual(
            [describe(slot) for slot in range(6)],
            [
                ("diF-TES-ADT 1a", "ADC0 CH0", ("U4", "U3")),
                ("diF-TES-ADT 1b", "ADC0 CH1", ("U4", "U3")),
                ("diF-TES-ADT 2a", "ADC0 CH2", ("U0", "U1")),
                ("diF-TES-ADT 2b", "ADC1 CH0", ("U0", "U1")),
                ("diF-TES-ADT 3a", "ADC1 CH1", ("U5", "U2")),
                ("diF-TES-ADT 3b", "ADC1 CH2", ("U5", "U2")),
            ],
        )

    def test_second_board_is_tips_pentacene_with_board_two_sensors(self) -> None:
        self.assertEqual(
            [describe(slot) for slot in range(6, 12)],
            [
                ("TIPs-pentacene 1a", "ADC2 CH0", ("F2_U4", "F2_U3")),
                ("TIPs-pentacene 1b", "ADC2 CH1", ("F2_U4", "F2_U3")),
                ("TIPs-pentacene 2a", "ADC2 CH2", ("F2_U0", "F2_U1")),
                ("TIPs-pentacene 2b", "ADC3 CH0", ("F2_U0", "F2_U1")),
                ("TIPs-pentacene 3a", "ADC3 CH1", ("F2_U5", "F2_U2")),
                ("TIPs-pentacene 3b", "ADC3 CH2", ("F2_U5", "F2_U2")),
            ],
        )

    def test_columns_list_materials_side_by_side_in_adc_order(self) -> None:
        self.assertEqual([material for material, _ in SAMPLE_COLUMNS], ["diF-TES-ADT", "TIPs-pentacene"])
        for _material, channels in SAMPLE_COLUMNS:
            slots = [channel.slot for channel in channels]
            self.assertEqual(slots, sorted(slots))
        self.assertEqual(sorted(SAMPLE_CHANNELS), list(range(12)))
