"""Dashboard: an at-a-glance summary of how the experiment is going right now."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..health_model import ERROR, OK, UNKNOWN, WARNING, HealthItem, Issue, format_rate
from ..plot_widget import SERIES_COLORS, PlotWidget
from ..protocol import HEATER_SENSOR_IDS, TEMP_SENSOR_LABELS, AMBIENT_SENSOR_IDS, PidTelemetryPacket, TelemetryPacket
from ..sample_layout import SAMPLE_COLUMNS
from ..telemetry_history import TelemetryHistorySnapshot, adc_point_for_mode
from .widgets import Panel

_BOARDS = (("F1", range(0, 6)), ("F2", range(6, 12)))
# Compact so the whole summary fits one screen; double-click opens the full plot.
_PLOT_HEIGHT = 170


def _set_state(widget: QWidget, state: str) -> None:
    if widget.property("state") == state:
        return
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class DashboardPage(QWidget):
    def __init__(self, open_health: Callable[[], None], open_geiger_plot: Callable[[], None]) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        layout.addWidget(self._build_status(open_health))
        layout.addWidget(self._build_radiation(open_geiger_plot))
        layout.addWidget(self._build_samples())
        layout.addWidget(self._build_thermal())
        layout.addStretch(1)

    # --- construction -------------------------------------------------------

    def _build_status(self, open_health: Callable[[], None]) -> QWidget:
        panel = Panel()
        row = QHBoxLayout()
        self.status_banner = QLabel("WAITING FOR TELEMETRY")
        self.status_banner.setObjectName("dashboardBanner")
        self.status_banner.setWordWrap(True)
        row.addWidget(self.status_banner, 1)
        health_button = QPushButton("Health ›")
        health_button.clicked.connect(open_health)
        row.addWidget(health_button)
        panel.layout.addLayout(row)
        self.link_line = QLabel("No telemetry yet")
        self.link_line.setObjectName("smallNote")
        panel.layout.addWidget(self.link_line)
        return panel

    def _build_radiation(self, open_geiger_plot: Callable[[], None]) -> QWidget:
        panel = Panel("RADIATION")
        row = QHBoxLayout()
        row.setSpacing(16)
        numbers = QVBoxLayout()
        numbers.setSpacing(10)
        self.geiger_values: list[tuple[QLabel, QLabel]] = []
        for counter_id in range(2):
            title = QLabel(f"GEIGER {counter_id + 1}")
            title.setObjectName("dashboardLabel")
            value = QLabel("—")
            value.setObjectName("dashboardValue")
            detail = QLabel("")
            detail.setObjectName("smallNote")
            numbers.addWidget(title)
            numbers.addWidget(value)
            numbers.addWidget(detail)
            self.geiger_values.append((value, detail))
        numbers.addStretch(1)
        row.addLayout(numbers)
        self.geiger_plot = PlotWidget("Dose rate (CPS)", "No data", hover_label="CPS", interactive=False)
        self.geiger_plot.on_double_click = open_geiger_plot
        self.geiger_plot.setFixedHeight(_PLOT_HEIGHT)
        row.addWidget(self.geiger_plot, 1)
        panel.layout.addLayout(row)
        return panel

    def _build_samples(self) -> QWidget:
        panel = Panel("SAMPLES")
        columns = QHBoxLayout()
        columns.setSpacing(16)
        self.sample_values: dict[int, tuple[QLabel, str]] = {}
        self.sample_plots: list[tuple[PlotWidget, tuple]] = []
        for material, channels in SAMPLE_COLUMNS:
            column = QVBoxLayout()
            column.setSpacing(6)
            title = QLabel(material.upper())
            title.setObjectName("dashboardLabel")
            column.addWidget(title)
            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(4)
            for index, channel in enumerate(channels):
                # The swatch matches the channel's line in the plot below.
                swatch = f'<span style="color:{SERIES_COLORS[index]}">■</span> {channel.pair}{channel.device}'
                value = QLabel()
                value.setObjectName("dashboardSample")
                value.setTextFormat(Qt.RichText)
                value.setToolTip(f"{channel.name} · {channel.location}")
                grid.addWidget(value, index // 2, index % 2)
                self.sample_values[channel.slot] = (value, swatch)
                value.setText(f"{swatch}&nbsp;&nbsp;—")
            column.addLayout(grid)
            plot = PlotWidget("Volts (V)", "No data", hover_label="Volts", interactive=False, legend=False)
            plot.setFixedHeight(_PLOT_HEIGHT)
            column.addWidget(plot)
            self.sample_plots.append((plot, channels))
            columns.addLayout(column, 1)
        panel.layout.addLayout(columns)
        return panel

    def _build_thermal(self) -> QWidget:
        panel = Panel("THERMAL")
        self.board_lines: dict[str, QLabel] = {}
        self.heater_tiles: list[QLabel] = []
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)
        for row, (board, heaters) in enumerate(_BOARDS):
            board_label = QLabel(board)
            board_label.setObjectName("dashboardLabel")
            grid.addWidget(board_label, row * 2, 0)
            line = QLabel("—")
            line.setObjectName("dashboardThermalLine")
            grid.addWidget(line, row * 2, 1, 1, len(heaters))
            self.board_lines[board] = line
            for column, heater_id in enumerate(heaters):
                tile = QLabel(f"H{heater_id}\n—")
                tile.setObjectName("healthTile")
                tile.setAlignment(Qt.AlignCenter)
                tile.setMinimumWidth(96)
                tile.setFixedHeight(44)
                grid.addWidget(tile, row * 2 + 1, column + 1)
                self.heater_tiles.append(tile)
        grid.setColumnStretch(len(_BOARDS[0][1]) + 1, 1)
        panel.layout.addLayout(grid)
        return panel

    # --- updates --------------------------------------------------------------

    def show_status(self, issues: Sequence[Issue], items: Sequence[HealthItem], link_text: str) -> None:
        downlink_live = any(item.key == "downlink" and item.state == OK for item in items)
        if issues:
            errors = sum(issue.state == ERROR for issue in issues)
            warnings = len(issues) - errors
            counts = ", ".join(
                f"{count} {noun}{'' if count == 1 else 's'}"
                for count, noun in ((errors, "error"), (warnings, "warning"))
                if count
            )
            self.status_banner.setText(f"{counts.upper()}  ·  {issues[0].text}")
            _set_state(self.status_banner, ERROR if errors else WARNING)
        elif downlink_live:
            self.status_banner.setText("ALL SYSTEMS NOMINAL")
            _set_state(self.status_banner, OK)
        else:
            self.status_banner.setText("WAITING FOR TELEMETRY")
            _set_state(self.status_banner, UNKNOWN)
        self.link_line.setText(link_text)

    def show_packet(
        self,
        packet: TelemetryPacket,
        pid: PidTelemetryPacket | None,
        history: TelemetryHistorySnapshot,
        items: Sequence[HealthItem],
    ) -> None:
        states = {item.key: item.state for item in items}
        self._show_radiation(packet, history, states)
        self._show_samples(packet, history, states)
        self._show_thermal(packet, pid, states)

    def clear(self) -> None:
        self.geiger_plot.set_points([])
        for plot, _channels in self.sample_plots:
            plot.set_points([])

    def _show_radiation(self, packet: TelemetryPacket, history: TelemetryHistorySnapshot, states: dict) -> None:
        for counter_id, (value, detail) in enumerate(self.geiger_values):
            reading = packet.geiger_reading(counter_id)
            if reading is None or not reading.valid:
                value.setText("no response")
                detail.setText("")
            else:
                value.setText(f"{format_rate(reading.dose_rate_cps)} cps")
                detail.setText(f"HV {reading.hv_voltage} V · total {reading.total_dose_sv:.3g} Sv")
            _set_state(value, states.get(f"geiger:{counter_id}", UNKNOWN))
        self.geiger_plot.set_series(
            (("Geiger 1", history.geiger_points[0]), ("Geiger 2", history.geiger_points[1]))
        )

    def _show_samples(self, packet: TelemetryPacket, history: TelemetryHistorySnapshot, states: dict) -> None:
        readings = {reading.slot: reading for reading in packet.ad7177_readings}
        for slot, (label, swatch) in self.sample_values.items():
            reading = readings.get(slot)
            if reading is None or not packet.os_adc_valid(slot) or reading.has_error:
                label.setText(f"{swatch}&nbsp;&nbsp;—")
            else:
                label.setText(f"{swatch}&nbsp;&nbsp;{reading.voltage:+.4f} V")
            _set_state(label, states.get(f"adc:{slot}", UNKNOWN))
        for plot, channels in self.sample_plots:
            plot.set_series(tuple(
                (
                    f"{channel.pair}{channel.device}",
                    [adc_point_for_mode(entry, "voltage") for entry in history.adc_points[channel.slot]],
                )
                for channel in channels
            ))

    def _show_thermal(self, packet: TelemetryPacket, pid: PidTelemetryPacket | None, states: dict) -> None:
        heaters = pid.heaters if pid is not None else ()
        for heater_id, tile in enumerate(self.heater_tiles):
            reading = heaters[heater_id] if heater_id < len(heaters) else None
            if reading is None:
                tile.setText(f"H{heater_id}\n—")
            else:
                temperature = "—" if reading.temperature_c is None else f"{reading.temperature_c:.1f}"
                tile.setText(
                    f"H{heater_id}  {reading.duty_permille / 10.0:.0f}%\n"
                    f"{temperature} → {reading.target_c:.1f} °C"
                )
            _set_state(tile, states.get(f"heater:{heater_id}", UNKNOWN))

        for board, heater_ids in _BOARDS:
            samples = [
                temperature
                for heater_id in heater_ids
                if (temperature := packet.temperature_c(HEATER_SENSOR_IDS[heater_id])) is not None
            ]
            ambient = [
                f"{TEMP_SENSOR_LABELS[sensor].split('_', 1)[1]} {temperature:.1f} °C"
                for sensor in AMBIENT_SENSOR_IDS
                if TEMP_SENSOR_LABELS[sensor].startswith(board)
                and (temperature := packet.temperature_c(sensor)) is not None
            ]
            parts = [
                f"samples {min(samples):.1f}–{max(samples):.1f} °C (mean {sum(samples) / len(samples):.1f})"
                if samples else "samples —",
            ]
            board_heaters = [heaters[heater_id] for heater_id in heater_ids if heater_id < len(heaters)]
            if board_heaters:
                targets = sorted({reading.target_c for reading in board_heaters})
                parts.append(
                    f"target {targets[0]:.1f} °C" if len(targets) == 1
                    else f"targets {targets[0]:.1f}–{targets[-1]:.1f} °C"
                )
                mean_duty = sum(reading.duty_permille for reading in board_heaters) / len(board_heaters)
                parts.append(f"mean duty {mean_duty / 10.0:.0f}%")
            parts.append("ambient " + (", ".join(ambient) if ambient else "—"))
            self.board_lines[board].setText("  ·  ".join(parts))
