from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .protocol_constants import (
    AD7177_BIPOLAR_MIDSCALE,
    AD7177_CHANNEL_COUNT,
    AD7177_STATUS_ADC_ERROR,
    AD7177_STATUS_CHANNEL_MASK,
    AD7177_STATUS_CRC_ERROR,
    AD7177_STATUS_RDY,
    AD7177_STATUS_REG_ERROR,
    AD7177_VREF_VOLTS,
    PID_FLAG_ENABLED,
    PID_FLAG_MANUAL,
    PID_FLAG_SENSOR_MAPPED,
    PID_FLAG_SENSOR_VALID,
    PID_RESULT_NAMES,
    TELEMETRY_FLAG_TCP_LISTENING,
)


@dataclass(frozen=True)
class Ad7177Reading:
    slot: int
    adc_index: int
    channel_index: int
    word: int
    raw24: int
    status: int

    @property
    def rdy(self) -> bool:
        return (self.status & AD7177_STATUS_RDY) != 0

    @property
    def adc_error(self) -> bool:
        return (self.status & AD7177_STATUS_ADC_ERROR) != 0

    @property
    def crc_error(self) -> bool:
        return (self.status & AD7177_STATUS_CRC_ERROR) != 0

    @property
    def reg_error(self) -> bool:
        return (self.status & AD7177_STATUS_REG_ERROR) != 0

    @property
    def status_channel(self) -> int:
        return self.status & AD7177_STATUS_CHANNEL_MASK

    @property
    def has_error(self) -> bool:
        return self.adc_error or self.crc_error or self.reg_error

    @property
    def voltage(self) -> float:
        """Decode the bipolar (offset binary) code against the external VREF."""
        return (self.raw24 - AD7177_BIPOLAR_MIDSCALE) / AD7177_BIPOLAR_MIDSCALE * AD7177_VREF_VOLTS


@dataclass(frozen=True)
class GeigerReading:
    valid: int
    counter_id: int
    error_flags: int
    event_id: int
    dose_cps: float
    dose_rate_cps: float
    total_dose_sv: float
    dose_time_sec: int
    stats_time_sec: int
    hv_voltage: int
    stat_error_percent: int
    stat_cell_count: int

    @classmethod
    def unavailable(cls, counter_id: int) -> GeigerReading:
        return cls(0, counter_id, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class TelemetryPacket:
    version: int
    message_type: int
    flags: int
    payload_length: int
    timestamp: int
    counter: int
    health_code: int
    temperature_valid_mask: int
    temperatures: tuple[int, ...]
    heater_duty_permille: int
    os_adc_valid_mask: int
    os_adc_readings: tuple[int, ...]
    geiger_readings: tuple[GeigerReading, ...]

    def geiger_reading(self, counter_id: int) -> GeigerReading | None:
        return next(
            (reading for reading in self.geiger_readings if reading.counter_id == counter_id),
            None,
        )

    @property
    def primary_geiger(self) -> GeigerReading:
        return self.geiger_reading(0) or GeigerReading.unavailable(0)

    @property
    def geiger_valid(self) -> int:
        return self.primary_geiger.valid

    @property
    def geiger_error_flags(self) -> int:
        return self.primary_geiger.error_flags

    @property
    def geiger_event_id(self) -> int:
        return self.primary_geiger.event_id

    @property
    def geiger_dose_cps(self) -> float:
        return self.primary_geiger.dose_cps

    @property
    def geiger_dose_rate_cps(self) -> float:
        return self.primary_geiger.dose_rate_cps

    @property
    def geiger_total_dose_sv(self) -> float:
        return self.primary_geiger.total_dose_sv

    @property
    def geiger_dose_time_sec(self) -> int:
        return self.primary_geiger.dose_time_sec

    @property
    def geiger_stats_time_sec(self) -> int:
        return self.primary_geiger.stats_time_sec

    @property
    def geiger_hv_voltage(self) -> int:
        return self.primary_geiger.hv_voltage

    @property
    def geiger_stat_error_percent(self) -> int:
        return self.primary_geiger.stat_error_percent

    @property
    def geiger_stat_cell_count(self) -> int:
        return self.primary_geiger.stat_cell_count

    @property
    def tcp_status(self) -> int:
        return 4 if (self.flags & TELEMETRY_FLAG_TCP_LISTENING) else 0

    @property
    def seq(self) -> int:
        return self.counter

    @property
    def tick_10ms(self) -> int:
        return self.timestamp

    @property
    def tick_ms(self) -> int:
        return self.timestamp

    @property
    def ad7177_readings(self) -> tuple[Ad7177Reading, ...]:
        return tuple(
            Ad7177Reading(
                slot=index,
                adc_index=index // AD7177_CHANNEL_COUNT,
                channel_index=index % AD7177_CHANNEL_COUNT,
                word=word,
                raw24=(word >> 8) & 0x00FFFFFF,
                status=word & 0xFF,
            )
            for index, word in enumerate(self.os_adc_readings)
        )

    def ad7177_reading(self, index: int) -> Ad7177Reading:
        return self.ad7177_readings[index]

    def temperature_valid(self, index: int) -> bool:
        return (self.temperature_valid_mask & (1 << index)) != 0

    def temperature_c(self, index: int) -> float | None:
        if not self.temperature_valid(index):
            return None
        return self.temperatures[index] / 100.0

    def os_adc_valid(self, index: int) -> bool:
        return (self.os_adc_valid_mask & (1 << index)) != 0


@dataclass(frozen=True)
class HeaterPidReading:
    heater_id: int
    sensor_id: int
    flags: int
    target_milli_c: int
    measurement_milli_c: int
    duty_permille: int
    result: int
    proportional_term: float
    integral_term: float
    derivative_term: float
    output: float
    kp: float
    ki: float
    kd: float

    @property
    def sensor_mapped(self) -> bool:
        return (self.flags & PID_FLAG_SENSOR_MAPPED) != 0

    @property
    def sensor_valid(self) -> bool:
        return (self.flags & PID_FLAG_SENSOR_VALID) != 0

    @property
    def pid_enabled(self) -> bool:
        return (self.flags & PID_FLAG_ENABLED) != 0

    @property
    def manual(self) -> bool:
        return (self.flags & PID_FLAG_MANUAL) != 0

    @property
    def result_name(self) -> str:
        return PID_RESULT_NAMES.get(self.result, f"unknown ({self.result})")

    @property
    def temperature_c(self) -> float | None:
        if not self.sensor_valid:
            return None
        return self.measurement_milli_c / 1000.0

    @property
    def target_c(self) -> float:
        return self.target_milli_c / 1000.0


@dataclass(frozen=True)
class PidTelemetryPacket:
    version: int
    message_type: int
    flags: int
    payload_length: int
    timestamp: int
    counter: int
    heater_count: int
    record_size: int
    mapped_mask: int
    sensor_valid_mask: int
    pid_enabled_mask: int
    manual_mask: int
    initialized_mask: int
    fault_mask: int
    heaters: tuple[HeaterPidReading, ...]


@dataclass(frozen=True)
class CombinedTelemetryPacket:
    standard: TelemetryPacket
    pid: PidTelemetryPacket

    @property
    def flags(self) -> int:
        return self.standard.flags

    @property
    def timestamp(self) -> int:
        return self.standard.timestamp

    @property
    def counter(self) -> int:
        return self.standard.counter


def heater_pid_averages(
    heaters: Iterable[HeaterPidReading],
) -> tuple[float | None, float | None]:
    heater_list = tuple(heaters)
    temperature_values = [
        heater.measurement_milli_c / 1000.0
        for heater in heater_list
        if heater.sensor_valid
    ]
    duty_values = [float(heater.duty_permille) for heater in heater_list]
    average_temperature = (
        sum(temperature_values) / len(temperature_values)
        if temperature_values
        else None
    )
    average_duty = sum(duty_values) / len(duty_values) if duty_values else None
    return average_temperature, average_duty
