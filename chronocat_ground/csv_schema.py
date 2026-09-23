from __future__ import annotations

from datetime import datetime

from .protocol import (
    AD7177_CHANNEL_COUNT,
    CombinedTelemetryPacket,
    TELEMETRY_OS_ADC_COUNT,
    TELEMETRY_TEMP_COUNT,
    GeigerReading,
    PidTelemetryPacket,
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


def csv_fieldnames() -> list[str]:
    fields = [
        "received_at",
        "source",
        "flags",
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
        fields.append(f"{prefix}_volts")
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
    fields.extend(pid_csv_fieldnames())
    return fields


def packet_to_row(
    packet: TelemetryPacket,
    received_at: datetime,
    source: tuple[str, int] | str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "received_at": received_at.isoformat(timespec="microseconds"),
        "source": format_source(source),
        "flags": f"0x{packet.flags:04x}",
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
        row[f"{prefix}_volts"] = f"{reading.voltage:.6f}"
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


def pid_csv_fieldnames() -> list[str]:
    fields = [
        "pid_enabled_mask",
        "pid_manual_mask",
    ]
    for heater_id in range(12):
        prefix = f"heater_{heater_id}"
        fields.extend(
            [
                f"{prefix}_target_milli_c",
                f"{prefix}_duty_permille",
                f"{prefix}_result",
                f"{prefix}_proportional",
                f"{prefix}_integral",
                f"{prefix}_derivative",
                f"{prefix}_output",
                f"{prefix}_kp",
                f"{prefix}_ki",
                f"{prefix}_kd",
            ]
        )
    return fields


def pid_packet_to_row(
    packet: PidTelemetryPacket,
    received_at: datetime,
    source: tuple[str, int] | str,
) -> dict[str, object]:
    row: dict[str, object] = {
        "received_at": received_at.isoformat(timespec="microseconds"),
        "source": format_source(source),
        "pid_enabled_mask": f"0x{packet.pid_enabled_mask:04x}",
        "pid_manual_mask": f"0x{packet.manual_mask:04x}",
    }
    for index, heater in enumerate(packet.heaters):
        prefix = f"heater_{index}"
        row.update(
            {
                f"{prefix}_target_milli_c": heater.target_milli_c,
                f"{prefix}_duty_permille": heater.duty_permille,
                f"{prefix}_result": heater.result,
                f"{prefix}_proportional": f"{heater.proportional_term:.9g}",
                f"{prefix}_integral": f"{heater.integral_term:.9g}",
                f"{prefix}_derivative": f"{heater.derivative_term:.9g}",
                f"{prefix}_output": f"{heater.output:.9g}",
                f"{prefix}_kp": f"{heater.kp:.9g}",
                f"{prefix}_ki": f"{heater.ki:.9g}",
                f"{prefix}_kd": f"{heater.kd:.9g}",
            }
        )
    return row


def combined_packet_to_row(
    packet: CombinedTelemetryPacket,
    received_at: datetime,
    source: tuple[str, int] | str,
) -> dict[str, object]:
    row = packet_to_row(packet.standard, received_at, source)
    row.update(pid_packet_to_row(packet.pid, received_at, source))
    return row


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


def format_source(source: tuple[str, int] | str) -> str:
    """Return a stable CSV representation for either receiver source form."""
    if isinstance(source, tuple):
        return f"{source[0]}:{source[1]}"
    return source
