import unittest

from chronocat_ground.protocol_constants import TEMP_SENSOR_LABELS
from chronocat_ground.sample_layout import SAMPLE_CHANNELS, SAMPLE_COLUMNS, SIGNAL_SLOTS


def describe(slot: int) -> tuple[str, str, tuple[str, str]]:
    channel = SAMPLE_CHANNELS[slot]
    sensors = tuple(TEMP_SENSOR_LABELS[index] for index in channel.temperature_sensor_ids)
    return channel.name, channel.location, sensors


class SampleLayoutTest(unittest.TestCase):
    """Follows adc_mapping.txt (flight configuration)."""

    def test_board_one_is_tips_pentacene_with_board_one_sensors(self) -> None:
        self.assertEqual(
            [describe(slot) for slot in range(6)],
            [
                ("TIPs-pentacene 1a", "ADC0 CH0", ("F1_U4", "F1_U3")),
                ("TIPs-pentacene 1b", "ADC0 CH1", ("F1_U4", "F1_U3")),
                ("TIPs-pentacene 2a", "ADC0 CH2", ("F1_U0", "F1_U1")),
                ("TIPs-pentacene 2b", "ADC1 CH0", ("F1_U0", "F1_U1")),
                ("TIPs-pentacene 3a", "ADC1 CH1", ("F1_U5", "F1_U2")),
                ("TIPs-pentacene 3b", "ADC1 CH2", ("F1_U5", "F1_U2")),
            ],
        )

    def test_board_two_is_dif_tes_adt_with_board_two_sensors(self) -> None:
        self.assertEqual(
            [describe(slot) for slot in range(6, 12)],
            [
                ("diF-TES-ADT 1a", "ADC2 CH0", ("F2_U4", "F2_U3")),
                ("diF-TES-ADT 1b", "ADC2 CH1", ("F2_U4", "F2_U3")),
                ("diF-TES-ADT 2a", "ADC2 CH2", ("F2_U0", "F2_U1")),
                ("diF-TES-ADT 2b", "ADC3 CH0", ("F2_U0", "F2_U1")),
                ("diF-TES-ADT 3a", "ADC3 CH1", ("F2_U5", "F2_U2")),
                ("diF-TES-ADT 3b", "ADC3 CH2", ("F2_U5", "F2_U2")),
            ],
        )

    def test_only_the_five_measuring_channels_are_signals(self) -> None:
        self.assertEqual(
            [SAMPLE_CHANNELS[slot].location for slot in SIGNAL_SLOTS],
            ["ADC0 CH1", "ADC0 CH2", "ADC1 CH0", "ADC2 CH0", "ADC3 CH0"],
        )
        roles = {SAMPLE_CHANNELS[slot].location: SAMPLE_CHANNELS[slot].role for slot in range(12)}
        self.assertEqual(roles["ADC0 CH0"], "noise")
        self.assertEqual((roles["ADC1 CH1"], roles["ADC1 CH2"]), ("empty", "empty"))
        self.assertEqual((roles["ADC2 CH1"], roles["ADC2 CH2"]), ("not connected", "not connected"))
        self.assertEqual((roles["ADC3 CH1"], roles["ADC3 CH2"]), ("noise reference", "noise reference"))

    def test_samples_follow_the_mapping_file(self) -> None:
        self.assertEqual(SAMPLE_CHANNELS[2].pair_text, "AT-07-1 (OUT) ↔ AT-01-3 (IN, shielded)")
        self.assertEqual(SAMPLE_CHANNELS[9].pair_text, "AD-07-4 (OUT) ↔ CD-08-2 (OUT, shielded)")
        self.assertEqual(SAMPLE_CHANNELS[6].pair_text, "AT-07-3 (IN) ↔ AT-07-2 (IN, shielded)")
        self.assertEqual(SAMPLE_CHANNELS[10].pair_text, "no sample ↔ no sample")

    def test_columns_list_materials_side_by_side_in_adc_order(self) -> None:
        self.assertEqual([material for material, _ in SAMPLE_COLUMNS], ["TIPs-pentacene", "diF-TES-ADT"])
        for _material, channels in SAMPLE_COLUMNS:
            slots = [channel.slot for channel in channels]
            self.assertEqual(slots, sorted(slots))
        self.assertEqual(sorted(SAMPLE_CHANNELS), list(range(12)))
