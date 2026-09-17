from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass

from .protocol import TELEMETRY_OS_ADC_COUNT, TelemetryPacket
from .telemetry_db import TelemetryDb


HISTORY_LENGTH = 300


@dataclass(frozen=True)
class TelemetryHistorySnapshot:
    """Rolling data needed by the presentation layer after one packet."""

    packet_count: int
    geiger_points: tuple[Sequence[tuple[float, float, float]], ...]
    adc_points: tuple[Sequence[tuple[float, float, float]], ...]
    adc_average_points: Sequence[tuple[float, float, float]]


class TelemetryHistory:
    """Own rolling telemetry history and its durable packet projection."""

    def __init__(self, database: TelemetryDb, history_length: int = HISTORY_LENGTH) -> None:
        self._database = database
        self._history_length = history_length
        self.packet_count = 0
        self._geiger_points = [deque(maxlen=history_length), deque(maxlen=history_length)]
        self._adc_points = [deque(maxlen=history_length) for _ in range(TELEMETRY_OS_ADC_COUNT)]
        self._adc_average_points = deque(maxlen=history_length)
        self.database_error: str | None = None

    def record(
        self,
        packet: TelemetryPacket,
        received_monotonic: float,
        received_wall: float,
    ) -> TelemetryHistorySnapshot:
        self.packet_count += 1

        geiger_rows = []
        for counter_id in range(2):
            reading = packet.geiger_reading(counter_id)
            if reading is None or not reading.valid:
                continue
            self._geiger_points[counter_id].append(
                (received_monotonic, received_wall, reading.dose_rate_cps)
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
                )
            )

        adc_rows = []
        adc_readings = packet.ad7177_readings
        for reading in adc_readings:
            if not packet.os_adc_valid(reading.slot):
                continue
            self._adc_points[reading.slot].append(
                (received_monotonic, received_wall, float(reading.raw24))
            )
            adc_rows.append((packet.timestamp, reading.slot, reading.raw24, received_wall))

        valid_adc_values = [
            reading.raw24
            for reading in adc_readings
            if packet.os_adc_valid(reading.slot) and reading.word != 0 and not reading.has_error
        ]
        if valid_adc_values:
            self._adc_average_points.append(
                (received_monotonic, received_wall, sum(valid_adc_values) / len(valid_adc_values))
            )

        temperature_rows = [
            (packet.timestamp, received_wall, slot, temperature_c)
            for slot in range(len(packet.temperatures))
            if (temperature_c := packet.temperature_c(slot)) is not None
        ]
        try:
            self._database.insert_packet(adc_rows, geiger_rows, temperature_rows)
        except RuntimeError as exc:
            self.database_error = str(exc)

        return TelemetryHistorySnapshot(
            self.packet_count,
            tuple(self._geiger_points),
            tuple(self._adc_points),
            self._adc_average_points,
        )

    def set_database(self, database: TelemetryDb) -> None:
        """Switch persistence after the active database has been archived."""
        self._database = database

    def adc_points(self, slot: int):
        return self._adc_points[slot]

    def geiger_points(self, counter_id: int):
        return self._geiger_points[counter_id]
