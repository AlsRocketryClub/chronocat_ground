from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .protocol import (
    AD7177_CHANNEL_COUNT,
    TELEMETRY_OS_ADC_COUNT,
    TELEMETRY_TEMP_COUNT,
    GeigerReading,
    TelemetryPacket,
    ad7177_status_names,
    geiger_error_names,
    tcp_status_name,
    telemetry_health_name,
)

GEIGER_CSV_FIELDS = [
    "valid",
    "counter_id",
    "error_flags",
    "error_names",
    "event_id",
    "dose_cps",
    "dose_rate_cps",
    "total_dose_sv",
    "dose_time_sec",
    "stats_time_sec",
    "hv_voltage",
    "stat_error_percent",
    "stat_cell_count",
]

CSV_MODE_FULL = "full"
CSV_MODE_GEIGER_ONLY = "geiger-only"

GEIGER_ONLY_CSV_FIELDS = [
    "received_at",
    "counter_id",
    "error_flags",
    "event_id",
    "dose_cps",
    "dose_time_sec",
    "total_dose_sv",
    "dose_rate_cps",
    "stats_time_sec",
    "hv_voltage",
    "stat_error_percent",
    "stat_cell_count",
]


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
        "packet_timestamp_ms",
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
        adc_index = (index - 1) // AD7177_CHANNEL_COUNT
        channel_index = (index - 1) % AD7177_CHANNEL_COUNT
        prefix = f"ad7177_adc_{adc_index}_ch_{channel_index}"
        fields.append(f"{prefix}_word")
        fields.append(f"{prefix}_raw24")
        fields.append(f"{prefix}_status")
        fields.append(f"{prefix}_status_names")

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
    for counter_id in (0, 1):
        fields.extend(f"geiger_{counter_id}_{field}" for field in GEIGER_CSV_FIELDS)
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
        "packet_timestamp_ms": packet.timestamp,
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

    for reading in packet.ad7177_readings:
        prefix = f"ad7177_adc_{reading.adc_index}_ch_{reading.channel_index}"
        row[f"{prefix}_word"] = f"0x{reading.word:08x}"
        row[f"{prefix}_raw24"] = reading.raw24
        row[f"{prefix}_status"] = f"0x{reading.status:02x}"
        row[f"{prefix}_status_names"] = ad7177_status_names(reading.status)

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
    for counter_id in (0, 1):
        add_geiger_reading_to_row(row, counter_id, packet.geiger_reading(counter_id))
    return row


def packet_to_geiger_rows(
    packet: TelemetryPacket, received_at: datetime
) -> list[dict[str, object]]:
    timestamp = received_at.isoformat(timespec="microseconds")
    rows: list[dict[str, object]] = []

    for reading in packet.geiger_readings:
        if not reading.valid:
            continue
        rows.append(
            {
                "received_at": timestamp,
                "counter_id": reading.counter_id,
                "error_flags": f"0x{reading.error_flags:04x}",
                "event_id": reading.event_id,
                "dose_cps": f"{reading.dose_cps:.17g}",
                "dose_time_sec": reading.dose_time_sec,
                "total_dose_sv": f"{reading.total_dose_sv:.9g}",
                "dose_rate_cps": f"{reading.dose_rate_cps:.9g}",
                "stats_time_sec": reading.stats_time_sec,
                "hv_voltage": reading.hv_voltage,
                "stat_error_percent": reading.stat_error_percent,
                "stat_cell_count": reading.stat_cell_count,
            }
        )
    return rows


def add_geiger_reading_to_row(
    row: dict[str, object], counter_id: int, reading: GeigerReading | None
) -> None:
    prefix = f"geiger_{counter_id}"
    if reading is None:
        for field in GEIGER_CSV_FIELDS:
            row[f"{prefix}_{field}"] = ""
        return

    row[f"{prefix}_valid"] = reading.valid
    row[f"{prefix}_counter_id"] = reading.counter_id
    row[f"{prefix}_error_flags"] = f"0x{reading.error_flags:04x}"
    row[f"{prefix}_error_names"] = geiger_error_names(reading.error_flags)
    row[f"{prefix}_event_id"] = reading.event_id
    row[f"{prefix}_dose_cps"] = f"{reading.dose_cps:.17g}"
    row[f"{prefix}_dose_rate_cps"] = f"{reading.dose_rate_cps:.9g}"
    row[f"{prefix}_total_dose_sv"] = f"{reading.total_dose_sv:.9g}"
    row[f"{prefix}_dose_time_sec"] = reading.dose_time_sec
    row[f"{prefix}_stats_time_sec"] = reading.stats_time_sec
    row[f"{prefix}_hv_voltage"] = reading.hv_voltage
    row[f"{prefix}_stat_error_percent"] = reading.stat_error_percent
    row[f"{prefix}_stat_cell_count"] = reading.stat_cell_count


def normalize_source(source: tuple[str, int] | str) -> tuple[str, int | str]:
    if isinstance(source, tuple):
        return source

    endpoint = source.split(" ", 1)[0]
    try:
        host, port = endpoint.rsplit(":", 1)
        return host, int(port)
    except ValueError:
        return source, ""


class TelemetryCsvLogger:
    def __init__(self, path: Path | None = None, mode: str = CSV_MODE_FULL) -> None:
        if mode not in (CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY):
            raise ValueError(f"unsupported CSV mode {mode!r}")
        self.path = path or default_output_path()
        self.mode = mode
        self.file: TextIO | None = None
        self.writer: csv.DictWriter | None = None
        self.packet_count = 0
        self.last_geiger_samples: dict[int, tuple[object, ...]] = {}

    @property
    def active(self) -> bool:
        return self.file is not None

    def start(self) -> None:
        if self.file is not None:
            return
        self.last_geiger_samples.clear()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open("w", newline="", encoding="utf-8")
        fieldnames = (
            GEIGER_ONLY_CSV_FIELDS
            if self.mode == CSV_MODE_GEIGER_ONLY
            else csv_fieldnames()
        )
        self.writer = csv.DictWriter(self.file, fieldnames=fieldnames)
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
        timestamp = received_at or datetime.now()
        if self.mode == CSV_MODE_GEIGER_ONLY:
            rows = packet_to_geiger_rows(packet, timestamp)
            rows = [
                row
                for row in rows
                if self.last_geiger_samples.get(int(row["counter_id"]))
                != tuple(row[field] for field in GEIGER_ONLY_CSV_FIELDS[2:])
            ]
            if not rows:
                return
            self.writer.writerows(rows)
            for row in rows:
                self.last_geiger_samples[int(row["counter_id"])] = tuple(
                    row[field] for field in GEIGER_ONLY_CSV_FIELDS[2:]
                )
        else:
            self.writer.writerow(packet_to_row(packet, timestamp, source))
        self.file.flush()
        self.packet_count += 1

    def stop(self) -> None:
        if self.file is None:
            return
        self.file.close()
        self.file = None
        self.writer = None
