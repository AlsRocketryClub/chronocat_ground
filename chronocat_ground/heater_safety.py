from __future__ import annotations

from .protocol import TelemetryPacket
from .protocol_constants import HEATER_SENSOR_IDS


HEATER_COUNT = 12
MAX_SAFE_TEMPERATURE_C = 65.0

# Sensor indices follow the firmware's two-board TMP117 ordering. Keep this
# mapping explicit so the heater page remains readable without relying on
# heater-number arithmetic or sensor discovery order.
if len(HEATER_SENSOR_IDS) != HEATER_COUNT:
    raise RuntimeError("heater temperature mapping must cover every heater")


def all_heaters_safe(packet: TelemetryPacket) -> bool:
    """Return whether the documented global heater-on interlock is satisfied."""
    return all(
        (temperature := packet.temperature_c(index)) is not None
        and temperature < MAX_SAFE_TEMPERATURE_C
        for index in range(HEATER_COUNT)
    )
