from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
import math

from .protocol import TELEMETRY_OS_ADC_COUNT, PidTelemetryPacket, TelemetryPacket
from .telemetry_db import TelemetryDb


HISTORY_LENGTH = 300
# The Radiation page offers a one-hour window; at 1 Hz that is 3600 points.
GEIGER_HISTORY_LENGTH = 3600


def adc_point_for_mode(
    entry: tuple[float, float, float, int], mode: str
) -> tuple[float, float, float]:
    """Project a stored (mono, wall, voltage, raw24) sample to the active display mode."""
    monotonic, wall, voltage, raw24 = entry
    return (monotonic, wall, float(raw24) if mode == "raw" else voltage)


@dataclass(frozen=True)
class TelemetryHistorySnapshot:
    """Rolling data needed by the presentation layer after one packet."""

    packet_count: int
    geiger_points: tuple[Sequence[tuple[float, float, float]], ...]
    # Absolute statistical error (cps) and the detector's user dose ("Dose",
    # zeroed by "reset dose"), aligned with geiger_points.
    geiger_error_points: tuple[Sequence[tuple[float, float, float]], ...]
    geiger_dose_points: tuple[Sequence[tuple[float, float, float]], ...]
    adc_points: tuple[Sequence[tuple[float, float, float, int]], ...]


class TelemetryHistory:
    """Own rolling telemetry history and its durable packet projection."""

    def __init__(self, database: TelemetryDb, history_length: int = HISTORY_LENGTH) -> None:
        self._database = database
        self._history_length = history_length
        self.packet_count = 0
        geiger_length = max(history_length, GEIGER_HISTORY_LENGTH)
        self._geiger_points = [deque(maxlen=geiger_length) for _ in range(2)]
        self._geiger_error_points = [deque(maxlen=geiger_length) for _ in range(2)]
        self._geiger_dose_points = [deque(maxlen=geiger_length) for _ in range(2)]
        self._adc_points = [deque(maxlen=history_length) for _ in range(TELEMETRY_OS_ADC_COUNT)]
        self.database_error: str | None = None

    def record(
        self,
        packet: TelemetryPacket,
        received_monotonic: float,
        received_wall: float,
        pid: PidTelemetryPacket | None = None,
    ) -> TelemetryHistorySnapshot:
        self.packet_count += 1

        geiger_rows = []
        for counter_id in range(2):
            reading = packet.geiger_reading(counter_id)
            if reading is None or not reading.valid:
                for points in (self._geiger_points, self._geiger_error_points, self._geiger_dose_points):
                    points[counter_id].append((received_monotonic, received_wall, math.nan))
                if reading is not None:
                    # Kept so a detector that stopped answering shows in the record.
                    geiger_rows.append(
                        (packet.timestamp, received_wall, counter_id, None, None, None, None, None,
                         0, reading.error_flags, None, None, None, None)
                    )
                continue
            self._geiger_points[counter_id].append(
                (received_monotonic, received_wall, reading.dose_rate_cps)
            )
            self._geiger_error_points[counter_id].append(
                (
                    received_monotonic,
                    received_wall,
                    reading.dose_rate_cps * reading.stat_error_percent / 100.0,
                )
            )
            self._geiger_dose_points[counter_id].append(
                (received_monotonic, received_wall, reading.dose_cps)
            )
            geiger_rows.append(
                (
                    packet.timestamp,
                    received_wall,
                    counter_id,
                    reading.dose_rate_cps,
                    reading.total_dose_sv,
                    reading.dose_time_sec,
                    reading.hv_voltage,
                    reading.stat_error_percent,
                    1,
                    reading.error_flags,
                    reading.event_id,
                    reading.dose_cps,
                    reading.stats_time_sec,
                    reading.stat_cell_count,
                )
            )

        adc_rows = []
        adc_readings = packet.ad7177_readings
        for reading in adc_readings:
            valid = (
                packet.os_adc_valid(reading.slot)
                and reading.word != 0
                and not reading.has_error
            )
            # Every reading is stored with its status, so an overrange or a
            # missing channel can be told apart from a lost packet later.
            adc_rows.append(
                (packet.timestamp, reading.slot, reading.raw24, received_wall,
                 reading.status, 1 if valid else 0)
            )
            if not valid:
                self._adc_points[reading.slot].append(
                    (received_monotonic, received_wall, math.nan, math.nan)
                )
                continue
            self._adc_points[reading.slot].append(
                (received_monotonic, received_wall, reading.voltage, reading.raw24)
            )

        temperature_rows = [
            (packet.timestamp, received_wall, slot, temperature_c)
            for slot in range(len(packet.temperatures))
            if (temperature_c := packet.temperature_c(slot)) is not None
        ]
        heater_rows = [
            (
                packet.timestamp, received_wall, heater_id, heater.duty_permille,
                heater.target_milli_c, heater.result, int(heater.pid_enabled),
                int(heater.manual), int(heater.sensor_valid), heater.proportional_term,
                heater.integral_term, heater.derivative_term, heater.output,
                heater.kp, heater.ki, heater.kd,
            )
            for heater_id, heater in enumerate(pid.heaters)
        ] if pid is not None else []
        packet_rows = [(
            packet.timestamp, received_wall, packet.counter, packet.version, packet.flags,
            packet.health_code, packet.temperature_valid_mask, packet.os_adc_valid_mask,
            pid.pid_enabled_mask if pid is not None else None,
            pid.manual_mask if pid is not None else None,
        )]
        try:
            self._database.insert_packet(
                adc_rows, geiger_rows, temperature_rows, heater_rows, packet_rows
            )
        except RuntimeError as exc:
            self.database_error = str(exc)

        return self.snapshot()

    def snapshot(self) -> TelemetryHistorySnapshot:
        return TelemetryHistorySnapshot(
            self.packet_count,
            tuple(self._geiger_points),
            tuple(self._geiger_error_points),
            tuple(self._geiger_dose_points),
            tuple(self._adc_points),
        )

    def seed(self, seed) -> None:
        """Fill the rolling plots with recent stored rows (see history_seed)."""
        for counter_id, points in seed.geiger.items():
            if counter_id >= len(self._geiger_points):
                continue
            for monotonic, wall, cps, error, dose in points:
                self._geiger_points[counter_id].append((monotonic, wall, cps))
                self._geiger_error_points[counter_id].append((monotonic, wall, error))
                self._geiger_dose_points[counter_id].append((monotonic, wall, dose))
        for slot, points in seed.adc.items():
            if slot < len(self._adc_points):
                self._adc_points[slot].extend(points)

    def clear(self) -> None:
        """Drop the in-memory history so a new database starts from a clean slate."""
        self.packet_count = 0
        for points in (
            *self._geiger_points,
            *self._geiger_error_points,
            *self._geiger_dose_points,
            *self._adc_points,
        ):
            points.clear()

    def set_database(self, database: TelemetryDb) -> None:
        """Switch persistence after the active database has been archived."""
        self._database = database

    def adc_points(self, slot: int):
        return self._adc_points[slot]

    def geiger_points(self, counter_id: int):
        return self._geiger_points[counter_id]
