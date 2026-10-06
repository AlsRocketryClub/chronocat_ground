"""Diagnostics: every decoded packet field, grouped by subsystem."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QVBoxLayout, QWidget

from ..health_model import format_uptime
from ..protocol import (
    HEATER_SENSOR_IDS,
    TELEMETRY_FLAG_ENABLED,
    TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET,
    TELEMETRY_FLAG_SD_LOG_ACTIVE,
    TELEMETRY_FLAG_SD_LOG_ERROR,
    TELEMETRY_FLAG_TCP_LISTENING,
    TEMP_SENSOR_LABELS,
    PidTelemetryPacket,
    TelemetryPacket,
    ad7177_status_names,
    geiger_error_names,
    geiger_flag_counter,
    tcp_status_name,
    telemetry_health_name,
)
from ..sample_layout import SAMPLE_CHANNELS
from .widgets import PAGE_SPACING, Panel, ValueTable

_FLAG_NAMES = (
    (TELEMETRY_FLAG_ENABLED, "telemetry enabled"),
    (TELEMETRY_FLAG_TCP_LISTENING, "TCP listening"),
    (TELEMETRY_FLAG_SD_LOG_ACTIVE, "SD log active"),
    (TELEMETRY_FLAG_SD_LOG_ERROR, "SD log error"),
    (TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET, "previous reset by watchdog"),
)
_GEIGER_FIELDS = (
    "Valid", "Counter ID", "Error flags", "Event ID", "User dose (since reset)", "Dose rate (CPS)",
    "Total before last reset (Sv)", "Dose time (s)", "Statistics time (s)", "HV (V)",
    "Statistical error (%)", "Statistical cells",
)
_HEATER_COLUMNS = (
    "Heater", "Sensor", "Mode", "Target (°C)", "Duty (‰)", "Result",
    "P", "I", "D", "Output", "Kp", "Ki", "Kd",
)


def _yes(value: bool) -> str:
    return "yes" if value else "no"


def _table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> ValueTable:
    table = ValueTable(rows, headers)
    # Columns stretch to the page width, so wide tables (13 heater columns)
    # never need to scroll sideways.
    table.horizontalHeader().setMinimumSectionSize(36)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.expand_to_contents()
    return table


def _panel(title: str, table: ValueTable) -> Panel:
    panel = Panel(title)
    panel.layout.addWidget(table)
    panel.layout.addStretch(1)  # side-by-side panels keep their tables at the top
    return panel


class DiagnosticsPage(QWidget):
    def __init__(self, command_panel: QWidget, log_panel: QWidget) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(PAGE_SPACING)

        self.packet_table = _table(
            [(name, "—") for name in (
                "Source", "Last seen", "Version", "Message type", "Payload length",
                "Counter", "Board timestamp (ms)", "Board uptime", "Flags",
            )],
            ("Field", "Value"),
        )
        self.system_table = _table(
            [(name, "—") for name in (
                "Health code", "TCP server", "Telemetry enabled", "SD log active",
                "SD log error", "Previous watchdog reset",
            )],
            ("Field", "Value"),
        )
        self.geiger_table = _table(
            [(name, "—", "—") for name in _GEIGER_FIELDS], ("Field", "Geiger 1", "Geiger 2")
        )
        self.temperature_table = _table(
            [(f"{index}", TEMP_SENSOR_LABELS[index], "—", "—", "—") for index in range(len(TEMP_SENSOR_LABELS))],
            ("Index", "Sensor", "Raw (c°C)", "°C", "Valid"),
        )
        self.adc_table = _table(
            [
                (f"{slot}", channel.location, channel.name, "—", "—", "—", "—", "—")
                for slot, channel in sorted(SAMPLE_CHANNELS.items())
            ],
            ("Slot", "Channel", "Sample", "Word", "Raw24", "Volts", "Status", "Valid"),
        )
        self.heater_table = _table(
            [
                (f"H{heater}", TEMP_SENSOR_LABELS[sensor], *("—",) * (len(_HEATER_COLUMNS) - 2))
                for heater, sensor in enumerate(HEATER_SENSOR_IDS)
            ],
            _HEATER_COLUMNS,
        )

        top = QGridLayout()
        top.setSpacing(PAGE_SPACING)
        top.addWidget(_panel("PACKET", self.packet_table), 0, 0)
        top.addWidget(_panel("SYSTEM", self.system_table), 0, 1)
        top.addWidget(_panel("GEIGER", self.geiger_table), 1, 0)
        top.addWidget(_panel("TEMPERATURES", self.temperature_table), 1, 1)
        top.setColumnStretch(0, 1)
        top.setColumnStretch(1, 1)
        layout.addLayout(top)
        layout.addWidget(_panel("SAMPLE ADCs", self.adc_table))
        layout.addWidget(_panel("HEATERS / PID", self.heater_table))
        layout.addWidget(command_panel)
        layout.addWidget(log_panel)
        layout.addStretch(1)

    def show_age(self, text: str) -> None:
        self.packet_table.set_value("Last seen", text)

    def show_packet(self, packet: TelemetryPacket, pid: PidTelemetryPacket | None, source: str) -> None:
        flags = [name for bit, name in _FLAG_NAMES if packet.flags & bit]
        for name, value in (
            ("Source", source),
            ("Version", str(packet.version)),
            ("Message type", str(packet.message_type)),
            ("Payload length", f"{packet.payload_length} bytes"),
            ("Counter", f"{packet.counter:,}"),
            ("Board timestamp (ms)", f"{packet.timestamp:,}"),
            ("Board uptime", format_uptime(packet.timestamp)),
            ("Flags", f"0x{packet.flags:04x} ({', '.join(flags) or 'none'})"),
        ):
            self.packet_table.set_value(name, value)

        for name, value in (
            ("Health code", f"{packet.health_code} ({telemetry_health_name(packet.health_code)})"),
            ("TCP server", f"{packet.tcp_status} ({tcp_status_name(packet.tcp_status)})"),
            ("Telemetry enabled", _yes(packet.flags & TELEMETRY_FLAG_ENABLED)),
            ("SD log active", _yes(packet.flags & TELEMETRY_FLAG_SD_LOG_ACTIVE)),
            ("SD log error", _yes(packet.flags & TELEMETRY_FLAG_SD_LOG_ERROR)),
            ("Previous watchdog reset", _yes(packet.flags & TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET)),
        ):
            self.system_table.set_value(name, value)

        for column, counter_id in ((1, 0), (2, 1)):
            reading = packet.geiger_reading(counter_id)
            if reading is None:
                for name in _GEIGER_FIELDS:
                    self.geiger_table.set_value(name, "not in packet", column)
                continue
            for name, value in zip(_GEIGER_FIELDS, (
                _yes(reading.valid), str(reading.counter_id),
                f"0x{reading.error_flags:04x} ({geiger_error_names(reading.error_flags)}; "
                f"counter {geiger_flag_counter(reading.error_flags)})",
                str(reading.event_id), f"{reading.dose_cps:.6g}", f"{reading.dose_rate_cps:.6g}",
                f"{reading.total_dose_sv:.6g}", str(reading.dose_time_sec), str(reading.stats_time_sec),
                str(reading.hv_voltage), str(reading.stat_error_percent), str(reading.stat_cell_count),
            )):
                self.geiger_table.set_value(name, value, column)

        for index, raw in enumerate(packet.temperatures):
            valid = packet.temperature_valid(index)
            self.temperature_table.set_value(str(index), str(raw), 2)
            self.temperature_table.set_value(str(index), f"{raw / 100:.2f}" if valid else "—", 3)
            self.temperature_table.set_value(str(index), _yes(valid), 4)
            self.temperature_table.set_state(str(index), "healthy" if valid else "warning", 4)

        for reading in packet.ad7177_readings:
            key = str(reading.slot)
            valid = packet.os_adc_valid(reading.slot)
            for column, value in (
                (3, f"0x{reading.word:08x}"),
                (4, f"0x{reading.raw24:06x}"),
                (5, f"{reading.voltage:+.6f}"),
                (6, f"0x{reading.status:02x} {ad7177_status_names(reading.status)}".strip()),
                (7, _yes(valid)),
            ):
                self.adc_table.set_value(key, value, column)
            self.adc_table.set_state(
                key, "error" if reading.has_error else ("healthy" if valid else "warning"), 7
            )

        if pid is None:
            return
        for heater_id, reading in enumerate(pid.heaters):
            key = f"H{heater_id}"
            mode = "manual" if reading.manual else ("PID" if reading.pid_enabled else "off")
            for column, value in enumerate((
                mode, f"{reading.target_c:.1f}", str(reading.duty_permille),
                f"{reading.result} {reading.result_name}",
                f"{reading.proportional_term:.4g}", f"{reading.integral_term:.4g}",
                f"{reading.derivative_term:.4g}", f"{reading.output:.4g}",
                f"{reading.kp:.4g}", f"{reading.ki:.4g}", f"{reading.kd:.4g}",
            ), start=2):
                self.heater_table.set_value(key, value, column)
