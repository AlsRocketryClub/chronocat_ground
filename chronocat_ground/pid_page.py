from __future__ import annotations

from collections import deque

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .heater_safety import HEATER_COUNT
from .pid_profiles import PID_PROFILES
from .plot_widget import PlotWidget
from .protocol import (
    HEATER_MANUAL_MAX_DUTY_PERMILLE,
    HeaterPidReading,
    PidTelemetryPacket,
    heater_pid_averages,
)
from .ui.pid_widgets import HeaterOverviewRow, SENSOR_NAMES


class PidPage(QWidget):
    target_requested = Signal(int, float)
    gain_requested = Signal(int, str, float)
    manual_duty_requested = Signal(int, int)
    return_pid_requested = Signal(int)
    all_off_requested = Signal()
    all_pid_requested = Signal(bool, float, str)

    def __init__(self) -> None:
        super().__init__()
        self._connected = False
        self._busy = False
        self.selected_heater = 0
        self.readings: list[HeaterPidReading | None] = [None] * HEATER_COUNT
        self.temperature_history = [deque(maxlen=180) for _ in range(HEATER_COUNT)]
        self.output_history = [deque(maxlen=180) for _ in range(HEATER_COUNT)]
        self.average_temperature_history = deque(maxlen=180)
        self.average_duty_history = deque(maxlen=180)
        self.rows: list[HeaterOverviewRow] = []
        self._mapped_mask = 0
        self._latest_pid_enabled_mask = 0
        self._global_operation_busy = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        root.addWidget(self._build_header())

        workspace = QSplitter(Qt.Horizontal)
        workspace.setChildrenCollapsible(False)
        workspace.setObjectName("pidWorkspace")
        workspace.addWidget(self._build_overview())
        workspace.addWidget(self._build_detail())
        workspace.setStretchFactor(0, 0)
        workspace.setStretchFactor(1, 1)
        workspace.setSizes([405, 715])
        root.addWidget(workspace, 1)

        self.set_connected(False)
        self.rows[0].set_selected(True)
        self._update_detail()

    def _build_header(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("pidGlobalControls")
        layout = QGridLayout(panel)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(5)

        layout.addWidget(QLabel("Setpoint"), 0, 0)
        self.global_target_spin = self._make_float_spin(0.0, 64.999, 20.0, 0.1)
        self.global_target_spin.setSuffix(" C")
        layout.addWidget(self.global_target_spin, 0, 1)

        layout.addWidget(QLabel("Profile"), 0, 2)
        self.profile_combo = QComboBox()
        for profile in PID_PROFILES:
            self.profile_combo.addItem(profile.name)
        self.profile_combo.setMinimumWidth(130)
        layout.addWidget(self.profile_combo, 0, 3)

        self.all_pid_button = QPushButton("Enable PID")
        self.all_pid_button.setObjectName("primaryButton")
        self.all_pid_button.clicked.connect(self._toggle_all_pid)
        layout.addWidget(self.all_pid_button, 0, 4)

        self.all_off_button = QPushButton("Turn all off")
        self.all_off_button.setObjectName("dangerButton")
        self.all_off_button.setMinimumWidth(105)
        self.all_off_button.clicked.connect(self.all_off_requested.emit)
        layout.addWidget(self.all_off_button, 0, 5)

        self.summary_label = QLabel("No PID data")
        self.summary_label.setObjectName("pidSummary")
        self.global_status = QLabel("")
        self.global_status.setObjectName("smallNote")
        layout.addWidget(self.global_status, 1, 0, 1, 4)
        layout.addWidget(self.summary_label, 1, 4, 1, 2)
        return panel

    def _build_overview(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("pidOverview")
        panel.setMinimumWidth(390)
        panel.setMaximumWidth(450)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)

        title_row = QHBoxLayout()
        title = QLabel("HEATERS")
        title.setObjectName("panelTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        layout.addLayout(title_row)

        columns = QHBoxLayout()
        columns.setContentsMargins(10, 0, 10, 0)
        for text, width in (("ID", 34), ("SENSOR", 62), ("TEMP", 72), ("DUTY", 54)):
            label = QLabel(text)
            label.setObjectName("pidColumnLabel")
            label.setFixedWidth(width)
            columns.addWidget(label)
        columns.addWidget(QLabel("STATE"))
        layout.addLayout(columns)

        rows_widget = QWidget()
        rows_layout = QVBoxLayout(rows_widget)
        rows_layout.setContentsMargins(0, 0, 0, 0)
        rows_layout.setSpacing(4)
        for heater_id in range(HEATER_COUNT):
            row = HeaterOverviewRow(heater_id)
            row.clicked.connect(self._select_heater)
            self.rows.append(row)
            rows_layout.addWidget(row)
        rows_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("pidOverviewScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(rows_widget)
        layout.addWidget(scroll, 1)
        return panel

    def _build_detail(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("pidDetail")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(8)

        heading = QHBoxLayout()
        self.detail_title = QLabel("H0")
        self.detail_title.setObjectName("pidDetailTitle")
        heading.addWidget(self.detail_title)
        self.detail_mapping = QLabel("—")
        self.detail_mapping.setObjectName("pidDetailMapping")
        heading.addWidget(self.detail_mapping)
        heading.addStretch(1)
        self.detail_status = QLabel("NO DATA")
        self.detail_status.setObjectName("pidBadge")
        heading.addWidget(self.detail_status)
        layout.addLayout(heading)

        self.detail_alert = QLabel("No data")
        self.detail_alert.setObjectName("pidAlert")
        self.detail_alert.setWordWrap(True)
        self.detail_alert.setVisible(False)
        layout.addWidget(self.detail_alert)

        plots = QSplitter(Qt.Horizontal)
        plots.setChildrenCollapsible(False)
        self.temperature_plot = PlotWidget(
            "Temperature (C)", "No data", hover_label="C",
            y_range=(-150.0, 150.0), min_y_range=5.0, monitor_mode=True,
        )
        self.output_plot = PlotWidget(
            "Duty (‰)", "No data", hover_label="‰",
            y_range=(0.0, 20.0), min_y_range=5.0, monitor_mode=True,
        )
        self.temperature_plot.setMinimumHeight(215)
        self.output_plot.setMinimumHeight(215)
        plots.addWidget(self.temperature_plot)
        plots.addWidget(self.output_plot)
        plots.setStretchFactor(0, 1)
        plots.setStretchFactor(1, 1)
        layout.addWidget(plots)

        average_title = QLabel("HEATER AVERAGES")
        average_title.setObjectName("panelTitle")
        layout.addWidget(average_title)
        average_plots = QSplitter(Qt.Horizontal)
        average_plots.setChildrenCollapsible(False)
        self.average_temperature_plot = PlotWidget(
            "Temperature (C)",
            "No data",
            hover_label="C",
            y_range=(-150.0, 150.0),
            min_y_range=5.0,
            monitor_mode=True,
        )
        self.average_duty_plot = PlotWidget(
            "Duty (‰)",
            "No data",
            hover_label="‰",
            y_range=(0.0, 20.0),
            min_y_range=5.0,
            monitor_mode=True,
        )
        self.average_temperature_plot.setMinimumHeight(175)
        self.average_duty_plot.setMinimumHeight(175)
        average_plots.addWidget(self.average_temperature_plot)
        average_plots.addWidget(self.average_duty_plot)
        average_plots.setStretchFactor(0, 1)
        average_plots.setStretchFactor(1, 1)
        layout.addWidget(average_plots)

        terms_panel = QFrame()
        terms_panel.setObjectName("pidMetrics")
        terms_layout = QHBoxLayout(terms_panel)
        terms_layout.setContentsMargins(10, 8, 10, 8)
        self.term_labels: dict[str, QLabel] = {}
        for name in ("P", "I", "D", "OUTPUT"):
            box = QVBoxLayout()
            title = QLabel(name)
            title.setObjectName("pidMetricLabel")
            value = QLabel("--")
            value.setObjectName("pidMetricValue")
            box.addWidget(title)
            box.addWidget(value)
            terms_layout.addLayout(box, 1)
            self.term_labels[name] = value
        layout.addWidget(terms_panel)

        layout.addWidget(self._build_controls())
        return panel

    def _build_controls(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("pidControls")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        title = QLabel("CONTROLS")
        title.setObjectName("panelTitle")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        self.target_spin = self._make_float_spin(0.0, 64.999, 20.0, 0.1)
        self.target_spin.setSuffix(" C")
        self.kp_spin = self._make_float_spin(0.0, 65.535, 2.25, 0.1)
        self.ki_spin = self._make_float_spin(0.0, 65.535, 0.051, 0.001)
        self.kd_spin = self._make_float_spin(0.0, 65.535, 4.0, 0.01)
        self.manual_spin = QSpinBox()
        self.manual_spin.setRange(0, HEATER_MANUAL_MAX_DUTY_PERMILLE)
        self.manual_spin.setSuffix(" / 1000")

        controls = (
            ("Target", self.target_spin, "Set", self._apply_target),
            ("Kp", self.kp_spin, "Set", self._apply_kp),
            ("Ki", self.ki_spin, "Set", self._apply_ki),
            ("Kd", self.kd_spin, "Set", self._apply_kd),
            ("Manual duty", self.manual_spin, "Set", self._apply_manual),
        )
        for row, (label_text, widget, button_text, callback) in enumerate(controls):
            grid.addWidget(QLabel(label_text), row, 0)
            grid.addWidget(widget, row, 1)
            grid.addWidget(self._button(button_text, callback), row, 2)

        self.return_button = self._button("Enable PID", self._return_pid)
        grid.addWidget(self.return_button, len(controls), 2)
        layout.addLayout(grid)
        self.command_status = QLabel("—")
        self.command_status.setObjectName("smallNote")
        layout.addWidget(self.command_status)
        return panel

    @staticmethod
    def _make_float_spin(minimum: float, maximum: float, value: float, step: float) -> QDoubleSpinBox:
        spin = QDoubleSpinBox()
        spin.setRange(minimum, maximum)
        spin.setDecimals(3)
        spin.setSingleStep(step)
        spin.setValue(value)
        return spin

    @staticmethod
    def _button(text: str, callback) -> QPushButton:
        button = QPushButton(text)
        button.clicked.connect(callback)
        return button

    def _select_heater(self, heater_id: int) -> None:
        self.selected_heater = heater_id
        for index, row in enumerate(self.rows):
            row.set_selected(index == heater_id)
        self._update_detail()

    def _apply_target(self) -> None:
        self.target_requested.emit(self.selected_heater, self.target_spin.value())

    def _apply_kp(self) -> None:
        self.gain_requested.emit(self.selected_heater, "kp", self.kp_spin.value())

    def _apply_ki(self) -> None:
        self.gain_requested.emit(self.selected_heater, "ki", self.ki_spin.value())

    def _apply_kd(self) -> None:
        self.gain_requested.emit(self.selected_heater, "kd", self.kd_spin.value())

    def _apply_manual(self) -> None:
        self.manual_duty_requested.emit(self.selected_heater, self.manual_spin.value())

    def _return_pid(self) -> None:
        self.return_pid_requested.emit(self.selected_heater)

    def _toggle_all_pid(self) -> None:
        activate = not self._all_pid_active()
        if activate:
            heater_ids = self.mapped_heater_ids()
            if not heater_ids:
                self.set_command_status("cannot activate PID: no mapped heaters")
                return
            answer = QMessageBox.question(
                self,
                "Activate mapped heaters",
                f"Apply the selected PID profile and common setpoint to "
                f"{len(heater_ids)} mapped heater(s)?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.all_pid_requested.emit(
            activate,
            self.global_target_spin.value(),
            self.profile_combo.currentText(),
        )

    def _all_pid_active(self) -> bool:
        return self._mapped_mask != 0 and (
            self._latest_pid_enabled_mask & self._mapped_mask
        ) == self._mapped_mask

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        self._update_enabled()

    def set_command_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_enabled()

    def _update_enabled(self) -> None:
        enabled = self._connected and not self._busy
        for button in self.findChildren(QPushButton):
            button.setEnabled(enabled)
        for widget in (self.target_spin, self.kp_spin, self.ki_spin, self.kd_spin, self.manual_spin):
            widget.setEnabled(enabled)
        self.global_target_spin.setEnabled(enabled and not self._global_operation_busy)
        self.profile_combo.setEnabled(enabled and not self._global_operation_busy)
        self.all_pid_button.setEnabled(enabled and not self._global_operation_busy)

    def set_command_status(self, text: str) -> None:
        self.command_status.setText(text)
        self.global_status.setText(text)

    def set_global_operation_busy(self, busy: bool) -> None:
        self._global_operation_busy = busy
        self._update_enabled()

    def mapped_heater_ids(self) -> list[int]:
        return [heater_id for heater_id in range(HEATER_COUNT) if self._mapped_mask & (1 << heater_id)]

    def update_packet(self, packet: PidTelemetryPacket) -> None:
        valid_count = 0
        enabled_count = 0
        fault_count = 0
        self._mapped_mask = packet.mapped_mask
        self._latest_pid_enabled_mask = packet.pid_enabled_mask
        for reading in packet.heaters:
            if not 0 <= reading.heater_id < HEATER_COUNT:
                continue
            heater_id = reading.heater_id
            self.readings[heater_id] = reading
            self.rows[heater_id].set_reading(reading)
            if reading.sensor_valid:
                valid_count += 1
                self.temperature_history[heater_id].append(
                    (packet.timestamp / 1000.0, reading.measurement_milli_c / 1000.0)
                )
            self.output_history[heater_id].append(
                (packet.timestamp / 1000.0, float(reading.duty_permille))
            )
            if reading.pid_enabled:
                enabled_count += 1
            if reading.result >= 5:
                fault_count += 1
        average_temperature, average_duty = heater_pid_averages(packet.heaters)
        timestamp = packet.timestamp / 1000.0
        if average_temperature is not None:
            self.average_temperature_history.append(
                (timestamp, average_temperature)
            )
            self.average_temperature_plot.set_points(
                list(self.average_temperature_history)
            )
        if average_duty is not None:
            self.average_duty_history.append((timestamp, average_duty))
            self.average_duty_plot.set_points(list(self.average_duty_history))
        self.summary_label.setText(
            f"PID {enabled_count}/12   |   sensors {valid_count}/12   |   "
            f"blocked/faulted {fault_count}/12   |   packet {packet.counter}"
        )
        if self._all_pid_active():
            self.all_pid_button.setText("Disable PID")
            self.global_status.setText("PID enabled on all mapped heaters")
        else:
            self.all_pid_button.setText("Enable PID")
        self._update_detail()

    def _update_detail(self) -> None:
        reading = self.readings[self.selected_heater]
        self.detail_title.setText(f"H{self.selected_heater}")
        if reading is None:
            self.detail_mapping.setText("—")
            self.detail_status.setText("NO DATA")
            self.detail_alert.setVisible(False)
            return

        sensor = "UNMAPPED"
        if reading.sensor_mapped and reading.sensor_id < len(SENSOR_NAMES):
            sensor = SENSOR_NAMES[reading.sensor_id]
        self.detail_mapping.setText(f"SENSOR {sensor}")
        self.detail_alert.setVisible(True)
        self.detail_status.setText(reading.result_name.upper())
        self.detail_status.setProperty("state", "fault" if reading.result >= 7 else "active" if reading.pid_enabled else "blocked")
        self.detail_status.style().unpolish(self.detail_status)
        self.detail_status.style().polish(self.detail_status)
        self.detail_alert.setText(
            f"{'PID' if reading.pid_enabled else 'MANUAL' if reading.manual else 'OFF'}  |  "
            f"Target {reading.target_c:.3f} C  |  Duty {reading.duty_permille}‰"
        )
        self.term_labels["P"].setText(f"{reading.proportional_term:.3f}")
        self.term_labels["I"].setText(f"{reading.integral_term:.3f}")
        self.term_labels["D"].setText(f"{reading.derivative_term:.3f}")
        self.term_labels["OUTPUT"].setText(f"{reading.output:.3f}")
        for spin, value in (
            (self.target_spin, reading.target_c),
            (self.kp_spin, reading.kp),
            (self.ki_spin, reading.ki),
            (self.kd_spin, reading.kd),
        ):
            if not spin.hasFocus():
                spin.setValue(value)
        self.temperature_plot.set_points(list(self.temperature_history[self.selected_heater]))
        self.output_plot.set_points(list(self.output_history[self.selected_heater]))
