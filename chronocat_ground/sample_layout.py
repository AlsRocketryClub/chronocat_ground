"""Which material, sample pair and temperature sensors belong to each ADC channel.

Each analog board measures the differential between two samples of one
material, device by device: device a of sample 1 against device a of sample 2,
and so on. Each sample sits on its own heater and temperature sensor, so every
ADC channel has two relevant temperatures.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol_constants import AD7177_CHANNEL_COUNT, TEMP_SENSOR_LABELS


@dataclass(frozen=True)
class SampleChannel:
    slot: int
    material: str
    pair: int
    device: str
    temperature_sensor_ids: tuple[int, int]

    @property
    def adc_index(self) -> int:
        return self.slot // AD7177_CHANNEL_COUNT

    @property
    def channel_index(self) -> int:
        return self.slot % AD7177_CHANNEL_COUNT

    @property
    def name(self) -> str:
        return f"{self.material} {self.pair}{self.device}"

    @property
    def location(self) -> str:
        return f"ADC{self.adc_index} CH{self.channel_index}"


# (material, first ADC on the board, sensor label prefix of the board)
_BOARDS = (
    ("diF-TES-ADT", 0, ""),
    ("TIPs-pentacene", 2, "F2_"),
)

# Sensor pairs in board channel order; consecutive channels share a pair and
# are devices a and b of that sample pair.
_PAIR_SENSORS = (("U4", "U3"), ("U0", "U1"), ("U5", "U2"))
_DEVICES = ("a", "b")


def _board_channels(material: str, first_adc: int, prefix: str) -> tuple[SampleChannel, ...]:
    channels = []
    for position in range(2 * AD7177_CHANNEL_COUNT):
        pair_index, device_index = divmod(position, len(_DEVICES))
        sensors = tuple(
            TEMP_SENSOR_LABELS.index(prefix + label) for label in _PAIR_SENSORS[pair_index]
        )
        channels.append(
            SampleChannel(
                slot=first_adc * AD7177_CHANNEL_COUNT + position,
                material=material,
                pair=pair_index + 1,
                device=_DEVICES[device_index],
                temperature_sensor_ids=sensors,
            )
        )
    return tuple(channels)


# One column per material, top to bottom in ADC channel order.
SAMPLE_COLUMNS = tuple(
    (material, _board_channels(material, first_adc, prefix))
    for material, first_adc, prefix in _BOARDS
)
SAMPLE_CHANNELS = {
    channel.slot: channel for _material, channels in SAMPLE_COLUMNS for channel in channels
}
