from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .protocol import (
    GEIGER_ERROR_NAMES,
    TELEMETRY_OS_ADC_COUNT,
    TELEMETRY_TEMP_COUNT,
    TelemetryPacket,
    tcp_status_name,
    telemetry_health_name,
)


def default_output_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path(f"telemetry_{timestamp}.csv")


def csv_fieldnames() -> list[str]:
    fields = [
        "received_at",
        "source_ip",
        "source_port",
        "version",
        "message_type",
        "flags",
        "payload_length",
        "packet_timestamp",
        "counter",
        "health_code",
        "health",
        "temperature_valid_mask",
    ]

    for index in range(1, TELEMETRY_TEMP_COUNT + 1):
        fields.append(f"temp_{index}_c")
        fields.append(f"temp_{index}_valid")

    fields.append("os_adc_valid_mask")

    for index in range(1, TELEMETRY_OS_ADC_COUNT + 1):
        fields.append(f"adc_{index}")
        fields.append(f"adc_{index}_valid")

    fields.append("tcp_status")
    fields.extend(
        [
            "geiger_valid",
            "geiger_error_flags",
            "geiger_error_names",
            "geiger_event_id",
            "geiger_dose_cps",
            "geiger_dose_rate_cps",
            "geiger_total_dose_sv",
            "geiger_dose_time_sec",
            "geiger_stats_time_sec",
            "geiger_hv_voltage",
            "geiger_stat_error_percent",
            "geiger_stat_cell_count",
        ]
    )
    return fields


def packet_to_row(packet: TelemetryPacket, received_at: datetime, source: tuple[str, int] | str) -> dict[str, object]:
    source_ip, source_port = normalize_source(source)
    row: dict[str, object] = {
        "received_at": received_at.isoformat(timespec="microseconds"),
        "source_ip": source_ip,
        "source_port": source_port,
        "version": packet.version,
        "message_type": packet.message_type,
        "flags": f"0x{packet.flags:04x}",
        "payload_length": packet.payload_length,
        "packet_timestamp": packet.timestamp,
        "counter": packet.counter,
        "health_code": packet.health_code,
        "health": telemetry_health_name(packet.health_code),
        "temperature_valid_mask": f"0x{packet.temperature_valid_mask:04x}",
    }

    for index, value in enumerate(packet.temperatures, start=1):
        zero_based = index - 1
        row[f"temp_{index}_c"] = f"{value / 100:.2f}"
        row[f"temp_{index}_valid"] = int(packet.temperature_valid(zero_based))

    row["os_adc_valid_mask"] = f"0x{packet.os_adc_valid_mask:04x}"

    for index, value in enumerate(packet.os_adc_readings, start=1):
        zero_based = index - 1
        row[f"adc_{index}"] = value
        row[f"adc_{index}_valid"] = int(packet.os_adc_valid(zero_based))

    row["tcp_status"] = tcp_status_name(packet.tcp_status)
    row["geiger_valid"] = packet.geiger_valid
    row["geiger_error_flags"] = f"0x{packet.geiger_error_flags:04x}"
    row["geiger_error_names"] = geiger_error_names(packet.geiger_error_flags)
    row["geiger_event_id"] = packet.geiger_event_id
    row["geiger_dose_cps"] = f"{packet.geiger_dose_cps:.17g}"
    row["geiger_dose_rate_cps"] = f"{packet.geiger_dose_rate_cps:.9g}"
    row["geiger_total_dose_sv"] = f"{packet.geiger_total_dose_sv:.9g}"
    row["geiger_dose_time_sec"] = packet.geiger_dose_time_sec
    row["geiger_stats_time_sec"] = packet.geiger_stats_time_sec
    row["geiger_hv_voltage"] = packet.geiger_hv_voltage
    row["geiger_stat_error_percent"] = packet.geiger_stat_error_percent
    row["geiger_stat_cell_count"] = packet.geiger_stat_cell_count
    return row


def normalize_source(source: tuple[str, int] | str) -> tuple[str, int | str]:
    if isinstance(source, tuple):
        return source

    endpoint = source.split(" ", 1)[0]
    try:
        host, port = endpoint.rsplit(":", 1)
        return host, int(port)
    except ValueError:
        return source, ""


def geiger_error_names(error_flags: int) -> str:
    if not error_flags:
        return "ok"
    return ", ".join(
        name for mask, name in GEIGER_ERROR_NAMES.items()
        if mask and (error_flags & mask)
    )


class TelemetryCsvLogger:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_output_path()
        self.file: TextIO | None = None
        self.writer: csv.DictWriter | None = None
        self.packet_count = 0

    @property
    def active(self) -> bool:
        return self.file is not None

    def start(self) -> None:
        if self.file is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.file, fieldnames=csv_fieldnames())
        self.writer.writeheader()
        self.file.flush()

    def write_packet(
        self,
        packet: TelemetryPacket,
        source: tuple[str, int] | str,
        received_at: datetime | None = None,
    ) -> None:
        if self.file is None or self.writer is None:
            raise RuntimeError("CSV logger is not active")
        self.writer.writerow(packet_to_row(packet, received_at or datetime.now(), source))
        self.file.flush()
        self.packet_count += 1

    def stop(self) -> None:
        if self.file is None:
            return
        self.file.close()
        self.file = None
        self.writer = None
