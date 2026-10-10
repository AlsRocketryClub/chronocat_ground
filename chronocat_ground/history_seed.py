"""Read the recent past back from the database so live plots resume on startup.

Rolling plots run on the monotonic clock of the current session; stored rows
carry wall-clock receive times. A row received `age` seconds ago is placed
`age` seconds before the current monotonic time, so the time the ground
station was off shows as a gap rather than being squeezed out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import sqlite3

from .protocol import AD7177_BIPOLAR_MIDSCALE, AD7177_VREF_VOLTS, HEATER_SENSOR_IDS
from .telemetry_db import GOOD_ROW

SEED_WINDOW_S = 3600.0


@dataclass
class SeedHistory:
    """Rows newer than the cutoff, in arrival order, with walls already moved to monotonic."""

    # counter_id -> [(monotonic, wall, cps, error_cps, user_dose)]
    geiger: dict[int, list[tuple[float, float, float, float, float]]] = field(default_factory=dict)
    # slot -> [(monotonic, wall, volts, raw24)]
    adc: dict[int, list[tuple[float, float, float, float]]] = field(default_factory=dict)
    # One entry per packet: (monotonic, {sensor: °C}, {heater: duty ‰})
    packets: list[tuple[float, dict[int, float], dict[int, int]]] = field(default_factory=list)


def _number(value) -> float:
    return math.nan if value is None else float(value)


def load_seed_history(
    connection: sqlite3.Connection,
    now_wall: float,
    now_monotonic: float,
    window_s: float = SEED_WINDOW_S,
) -> SeedHistory:
    cutoff = now_wall - window_s
    seed = SeedHistory()

    def to_monotonic(wall: float) -> float:
        return now_monotonic - (now_wall - wall)

    for wall, counter_id, cps, error_percent, user_dose, good in connection.execute(
        f"SELECT received_wall, counter_id, dose_rate_cps, stat_error_percent, user_dose, {GOOD_ROW} "
        "FROM geiger WHERE received_wall >= ? ORDER BY rowid",
        (cutoff,),
    ):
        if good and cps is not None:
            error = cps * _number(error_percent) / 100.0
            point = (to_monotonic(wall), wall, float(cps), error, _number(user_dose))
        else:
            point = (to_monotonic(wall), wall, math.nan, math.nan, math.nan)
        seed.geiger.setdefault(counter_id, []).append(point)

    for wall, slot, raw24, good in connection.execute(
        f"SELECT received_wall, slot, raw24, {GOOD_ROW} FROM adc "
        "WHERE received_wall >= ? ORDER BY rowid",
        (cutoff,),
    ):
        if good and raw24 is not None:
            volts = (raw24 - AD7177_BIPOLAR_MIDSCALE) / AD7177_BIPOLAR_MIDSCALE * AD7177_VREF_VOLTS
            point = (to_monotonic(wall), wall, volts, float(raw24))
        else:
            point = (to_monotonic(wall), wall, math.nan, math.nan)
        seed.adc.setdefault(slot, []).append(point)

    # Temperatures and duties grouped per packet; a sensor without a row had
    # no valid reading in that packet.
    by_wall: dict[float, tuple[dict[int, float], dict[int, int]]] = {}
    for wall, slot, temperature in connection.execute(
        "SELECT received_wall, slot, temperature_c FROM temperature "
        "WHERE received_wall >= ? ORDER BY rowid",
        (cutoff,),
    ):
        by_wall.setdefault(wall, ({}, {}))[0][slot] = temperature
    for wall, heater_id, duty in connection.execute(
        "SELECT received_wall, heater_id, duty_permille FROM heater "
        "WHERE received_wall >= ? ORDER BY rowid",
        (cutoff,),
    ):
        by_wall.setdefault(wall, ({}, {}))[1][heater_id] = duty
    for wall, _counter in connection.execute(
        "SELECT received_wall, counter FROM packet WHERE received_wall >= ? ORDER BY rowid",
        (cutoff,),
    ):
        by_wall.setdefault(wall, ({}, {}))
    seed.packets = [
        (to_monotonic(wall), temperatures, duties)
        for wall, (temperatures, duties) in sorted(by_wall.items())
    ]
    return seed


def heater_averages(temperatures: dict[int, float], duties: dict[int, int]) -> tuple[float | None, float | None]:
    """The heating page's averages for one stored packet (valid heater sensors, all duties)."""
    heater_temperatures = [temperatures[sensor] for sensor in HEATER_SENSOR_IDS if sensor in temperatures]
    average_temperature = (
        sum(heater_temperatures) / len(heater_temperatures) if heater_temperatures else None
    )
    average_duty = sum(duties.values()) / len(duties) if duties else None
    return average_temperature, average_duty
