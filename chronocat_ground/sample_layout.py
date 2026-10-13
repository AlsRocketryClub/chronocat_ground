"""Which material, sample pair and temperature sensors belong to each ADC channel.

Each analog board measures the differential between two samples, device by
device: device a of the pair against device b. Each sample sits on its own
heater and temperature sensor, so every ADC channel has two relevant
temperatures.

The samples fitted and what each channel is expected to show follow
adc_mapping.txt (flight configuration): not every channel carries a signal;
some are noise, empty, unconnected or a shorted noise reference.
"""

from __future__ import annotations

from dataclasses import dataclass

from .protocol_constants import AD7177_CHANNEL_COUNT, HEATER_SENSOR_IDS, TEMP_SENSOR_LABELS


@dataclass(frozen=True)
class Sample:
    """One sample at a sensor spot, as labelled in adc_mapping.txt."""

    sample_id: str  # material type and number, e.g. AT-07-1
    connection: str  # e.g. BC10
    device: str  # IN or OUT: inner or outer device on the sample
    shielded: bool = False

    @property
    def description(self) -> str:
        return f"{self.sample_id} ({self.device}{', shielded' if self.shielded else ''})"


# What a channel is expected to show. Only "signal" channels carry a reading.
ROLE_SIGNAL = "signal"
ROLE_NOISE = "noise"
ROLE_EMPTY = "empty"
ROLE_NOT_CONNECTED = "not connected"
ROLE_NOISE_REFERENCE = "noise reference"


@dataclass(frozen=True)
class SampleChannel:
    slot: int
    material: str
    pair: int
    device: str
    temperature_sensor_ids: tuple[int, int]
    role: str = ROLE_SIGNAL
    role_note: str = ""
    samples: tuple[Sample | None, Sample | None] = (None, None)

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

    @property
    def is_signal(self) -> bool:
        return self.role == ROLE_SIGNAL

    @property
    def pair_text(self) -> str:
        """The two samples this channel compares, e.g. 'AT-07-1 (OUT) ↔ AT-01-3 (IN, shielded)'."""
        return " ↔ ".join(
            sample.description if sample is not None else "no sample" for sample in self.samples
        )


# (material, first ADC on the board, sensor label prefix of the board)
_BOARDS = (
    ("TIPs-pentacene", 0, "F1_"),
    ("diF-TES-ADT", 2, "F2_"),
)

# Sensor pairs in board channel order; consecutive channels share a pair and
# are devices a and b of that sample pair.
_PAIR_SENSORS = (("U4", "U3"), ("U0", "U1"), ("U5", "U2"))
_DEVICES = ("a", "b")

# Samples fitted per sensor spot; U5 and U2 carry none on either board.
SAMPLES: dict[str, Sample | None] = {
    "F1_U4": Sample("AT-01-5", "BC80", "OUT"),
    "F1_U3": Sample("AT-02-3", "BC80", "OUT", shielded=True),
    "F1_U0": Sample("AT-07-1", "BC10", "OUT"),
    "F1_U1": Sample("AT-01-3", "BC10", "IN", shielded=True),
    "F1_U5": None,
    "F1_U2": None,
    "F2_U4": Sample("AT-07-3", "BC20", "IN"),
    "F2_U3": Sample("AT-07-2", "BC20", "IN", shielded=True),
    "F2_U0": Sample("AD-07-4", "BC40", "OUT"),
    "F2_U1": Sample("CD-08-2", "BC40", "OUT", shielded=True),
    "F2_U5": None,
    "F2_U2": None,
}

# Expected content per ADC slot (ADC n, channel c is slot 3n + c).
CHANNEL_ROLES: dict[int, tuple[str, str]] = {
    0: (ROLE_NOISE, "only ADC0 CH1 gives data for this pair"),
    1: (ROLE_SIGNAL, ""),
    2: (ROLE_SIGNAL, "should read the same as ADC1 CH0"),
    3: (ROLE_SIGNAL, "should read the same as ADC0 CH2"),
    4: (ROLE_EMPTY, "no sample fitted"),
    5: (ROLE_EMPTY, "no sample fitted"),
    6: (ROLE_SIGNAL, ""),
    7: (ROLE_NOT_CONNECTED, "not connected"),
    8: (ROLE_NOT_CONNECTED, "not connected"),
    9: (ROLE_SIGNAL, ""),
    10: (ROLE_NOISE_REFERENCE, "inputs shorted; noise reference"),
    11: (ROLE_NOISE_REFERENCE, "inputs shorted; noise reference"),
}


def _board_channels(material: str, first_adc: int, prefix: str) -> tuple[SampleChannel, ...]:
    channels = []
    for position in range(2 * AD7177_CHANNEL_COUNT):
        pair_index, device_index = divmod(position, len(_DEVICES))
        labels = tuple(prefix + label for label in _PAIR_SENSORS[pair_index])
        slot = first_adc * AD7177_CHANNEL_COUNT + position
        role, note = CHANNEL_ROLES[slot]
        channels.append(
            SampleChannel(
                slot=slot,
                material=material,
                pair=pair_index + 1,
                device=_DEVICES[device_index],
                temperature_sensor_ids=tuple(TEMP_SENSOR_LABELS.index(label) for label in labels),
                role=role,
                role_note=note,
                samples=tuple(SAMPLES[label] for label in labels),
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
SIGNAL_SLOTS = tuple(slot for slot, channel in sorted(SAMPLE_CHANNELS.items()) if channel.is_signal)

# Heaters under a fitted sample: the U5 and U2 spots (H2, H5, H8, H11) are empty.
HEATERS_WITH_SAMPLES = tuple(
    heater_id
    for heater_id, sensor in enumerate(HEATER_SENSOR_IDS)
    if SAMPLES.get(TEMP_SENSOR_LABELS[sensor]) is not None
)
