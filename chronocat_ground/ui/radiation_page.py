"""Radiation page: both detectors side by side, rate with its statistical error, total dose."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..health_model import ERROR, OK, UNKNOWN, WARNING, HealthItem, format_rate
from ..plot_widget import SERIES_COLORS, PlotWidget
from ..protocol import GEIGER_CALIBRATION_FLAGS, TelemetryPacket, geiger_error_names
from ..telemetry_history import TelemetryHistorySnapshot
from .widgets import PAGE_SPACING, Panel

_WINDOWS = (("5 min", 300.0), ("1 h", 3600.0))
_SERIES = ("Geiger 1", "Geiger 2")


def _set_state(widget: QWidget, state: str) -> None:
    if widget.property("state") == state:
        return
    widget.setProperty("state", state)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


class _DetectorPanel(Panel):
    def __init__(self, counter_id: int) -> None:
        super().__init__()
        header = QHBoxLayout()
        swatch = QLabel(
            f'<span style="color:{SERIES_COLORS[counter_id]}">■</span>&nbsp;GEIGER {counter_id + 1}'
        )
        swatch.setObjectName("panelTitle")
        header.addWidget(swatch)
        header.addStretch(1)
        self.status = QLabel("—")
        self.status.setObjectName("radiationStatus")
        header.addWidget(self.status)
        self.layout.addLayout(header)
        self.rate = QLabel("—")
        self.rate.setObjectName("dashboardValue")
        self.layout.addWidget(self.rate)
        self.summary = QLabel("")
        self.summary.setObjectName("radiationSummary")
        self.layout.addWidget(self.summary)
        self.detail = QLabel("")
        self.detail.setObjectName("smallNote")
        self.detail.setWordWrap(True)
        self.layout.addWidget(self.detail)

    def show_reading(self, reading, item: HealthItem | None) -> None:
        state = item.state if item is not None else UNKNOWN
        _set_state(self.rate, state)
        _set_state(self.status, state)
        if state == OK:
            self.status.setText("OK")
        elif state in (WARNING, ERROR):
            self.status.setText(item.reason)
        else:
            self.status.setText("no data")
        if reading is None or not reading.valid:
            self.rate.setText("no response")
            self.summary.setText("")
            self.detail.setText("")
            return
        error = reading.dose_rate_cps * reading.stat_error_percent / 100.0
        self.rate.setText(f"{format_rate(reading.dose_rate_cps)} ± {format_rate(error)} cps")
        self.summary.setText(f"HV {reading.hv_voltage} V  ·  total dose {reading.total_dose_sv:.4g} Sv")
        parts = [
            f"Statistical error {reading.stat_error_percent} % over {reading.stat_cell_count} cells "
            f"and {reading.stats_time_sec} s",
            f"dose accumulated over {reading.dose_time_sec} s",
            f"event {reading.event_id}",
        ]
        if reading.error_flags & GEIGER_CALIBRATION_FLAGS:
            parts.append(geiger_error_names(reading.error_flags & GEIGER_CALIBRATION_FLAGS))
        self.detail.setText("  ·  ".join(parts))


class RadiationPage(QWidget):
    def __init__(self, controls: QWidget, open_flight_history: Callable[[], None]) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(PAGE_SPACING)

        detectors = QHBoxLayout()
        detectors.setSpacing(PAGE_SPACING)
        self.detectors = [_DetectorPanel(counter_id) for counter_id in range(2)]
        for panel in self.detectors:
            detectors.addWidget(panel, 1)
        layout.addLayout(detectors)

        rate_panel = Panel()
        header = QHBoxLayout()
        title = QLabel("DOSE RATE")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        note = QLabel("shaded: ± statistical error")
        note.setObjectName("smallNote")
        header.addWidget(note)
        header.addStretch(1)
        self.window_buttons: dict[float, QPushButton] = {}
        for text, seconds in _WINDOWS:
            button = QPushButton(text)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, seconds=seconds: self.set_time_window(seconds))
            header.addWidget(button)
            self.window_buttons[seconds] = button
        flight = QPushButton("Whole flight ↗")
        flight.setObjectName("segmentButton")
        flight.setToolTip("Open the full history from the database")
        flight.clicked.connect(open_flight_history)
        header.addWidget(flight)
        rate_panel.layout.addLayout(header)
        self.rate_plot = PlotWidget("Dose rate (CPS)", "No data", hover_label="CPS", interactive=False)
        self.rate_plot.on_double_click = open_flight_history
        self.rate_plot.setFixedHeight(260)
        rate_panel.layout.addWidget(self.rate_plot)
        layout.addWidget(rate_panel)

        dose_panel = Panel("TOTAL DOSE")
        self.dose_plot = PlotWidget("Total dose (Sv)", "No data", hover_label="Sv", interactive=False)
        self.dose_plot.setFixedHeight(200)
        dose_panel.layout.addWidget(self.dose_plot)
        layout.addWidget(dose_panel)

        layout.addWidget(controls)
        layout.addStretch(1)
        self.set_time_window(_WINDOWS[0][1])

    def set_time_window(self, seconds: float) -> None:
        for window, button in self.window_buttons.items():
            button.setChecked(window == seconds)
        self.rate_plot.set_time_window(seconds)
        self.dose_plot.set_time_window(seconds)

    def show_packet(
        self, packet: TelemetryPacket, history: TelemetryHistorySnapshot, items: Sequence[HealthItem]
    ) -> None:
        by_key = {item.key: item for item in items}
        for counter_id, panel in enumerate(self.detectors):
            panel.show_reading(packet.geiger_reading(counter_id), by_key.get(f"geiger:{counter_id}"))
        self.rate_plot.set_series(
            tuple(zip(_SERIES, history.geiger_points)),
            bands=dict(zip(_SERIES, history.geiger_error_points)),
        )
        self.dose_plot.set_series(tuple(zip(_SERIES, history.geiger_dose_points)))

    def clear(self) -> None:
        self.rate_plot.set_series(())
        self.dose_plot.set_series(())
