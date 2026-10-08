from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from ..heater_safety import HEATER_SENSOR_IDS
from ..protocol import TEMP_SENSOR_LABELS, HeaterPidReading


# Heater feedback sensors are the first twelve, in firmware order.
SENSOR_NAMES = TEMP_SENSOR_LABELS[:12]


class HeaterOverviewRow(QFrame):
    clicked = Signal(int)

    def __init__(self, heater_id: int) -> None:
        super().__init__()
        self.heater_id = heater_id
        self.setObjectName("pidRow")
        self.setMinimumHeight(44)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        self.heater_label = QLabel(f"H{heater_id}")
        self.heater_label.setObjectName("pidRowHeater")
        self.heater_label.setFixedWidth(34)
        layout.addWidget(self.heater_label)

        self.sensor_label = QLabel(self._mapped_sensor_name())
        self.sensor_label.setObjectName("pidRowSensor")
        self.sensor_label.setFixedWidth(62)
        layout.addWidget(self.sensor_label)

        self.temperature_label = QLabel("—")
        self.temperature_label.setObjectName("pidRowTemperature")
        self.temperature_label.setFixedWidth(72)
        layout.addWidget(self.temperature_label)

        self.duty_label = QLabel("0.0%")
        self.duty_label.setObjectName("pidRowDuty")
        self.duty_label.setFixedWidth(54)
        layout.addWidget(self.duty_label)

        self.state_label = QLabel("—")
        self.state_label.setObjectName("pidBadge")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setFixedWidth(104)
        layout.addWidget(self.state_label)
        layout.addStretch(1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.heater_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self._refresh_style()

    def set_reading(self, reading: HeaterPidReading | None) -> None:
        if reading is None:
            self.sensor_label.setText(self._mapped_sensor_name())
            self.temperature_label.setText("—")
            self.duty_label.setText("0.0%")
            self.state_label.setText("—")
            self.setProperty("state", "waiting")
            self._refresh_style()
            return

        sensor = "UNMAPPED"
        if reading.sensor_mapped and reading.sensor_id < len(SENSOR_NAMES):
            sensor = SENSOR_NAMES[reading.sensor_id]
        temperature = "—"
        if reading.sensor_valid:
            temperature = f"{reading.measurement_milli_c / 1000.0:.2f} C"

        if reading.forced:
            state = "FORCED"
            state_class = "forced"
        elif not reading.sensor_mapped:
            state = "UNMAPPED"
            state_class = "blocked"
        elif reading.result >= 7:
            state = "FAULT"
            state_class = "fault"
        elif reading.result >= 5:
            state = "BLOCKED"
            state_class = "blocked"
        elif reading.pid_enabled:
            state = "PID"
            state_class = "active"
        elif reading.manual:
            state = "MANUAL"
            state_class = "manual"
        else:
            state = "OFF"
            state_class = "off"

        self.sensor_label.setText(sensor)
        self.temperature_label.setText(temperature)
        self.duty_label.setText(f"{reading.duty_permille / 10.0:.1f}%")
        self.state_label.setText(state)
        self.state_label.setToolTip(reading.result_name)
        self.setProperty("state", state_class)
        self._refresh_style()

    def _mapped_sensor_name(self) -> str:
        sensor_id = HEATER_SENSOR_IDS[self.heater_id]
        return SENSOR_NAMES[sensor_id]

    def _refresh_style(self) -> None:
        self.style().unpolish(self)
        self.style().polish(self)
        self.state_label.style().unpolish(self.state_label)
        self.state_label.style().polish(self.state_label)


class AmbientSensorRow(QFrame):
    """An ambient sensor in the heater list: a temperature only, no heater."""

    clicked = Signal(int)

    def __init__(self, sensor_id: int) -> None:
        super().__init__()
        self.sensor_id = sensor_id
        self.setObjectName("pidRow")
        self.setProperty("state", "waiting")
        self.setMinimumHeight(36)
        self.setCursor(Qt.PointingHandCursor)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 4, 10, 4)
        layout.setSpacing(8)

        label = QLabel("—")
        label.setObjectName("pidRowHeater")
        label.setFixedWidth(34)
        layout.addWidget(label)

        sensor = QLabel(TEMP_SENSOR_LABELS[sensor_id])
        sensor.setObjectName("pidRowSensor")
        sensor.setFixedWidth(62)
        layout.addWidget(sensor)

        self.temperature_label = QLabel("—")
        self.temperature_label.setObjectName("pidRowTemperature")
        self.temperature_label.setFixedWidth(72)
        layout.addWidget(self.temperature_label)

        duty = QLabel("")
        duty.setFixedWidth(54)
        layout.addWidget(duty)

        self.state_label = QLabel("—")
        self.state_label.setObjectName("pidBadge")
        self.state_label.setAlignment(Qt.AlignCenter)
        self.state_label.setFixedWidth(104)
        layout.addWidget(self.state_label)
        layout.addStretch(1)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.sensor_id)
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_temperature(self, temperature_c: float | None, received: bool) -> None:
        if not received:
            text, state, badge = "—", "waiting", "—"
        elif temperature_c is None:
            text, state, badge = "—", "off", "NO READING"
        else:
            text, state, badge = f"{temperature_c:.2f} C", "off", "AMBIENT"
        self.temperature_label.setText(text)
        self.state_label.setText(badge)
        self.state_label.setToolTip("ambient sensor, not paired with a heater")
        if self.property("state") != state:
            self.setProperty("state", state)
            self.style().unpolish(self)
            self.style().polish(self)
            self.state_label.style().unpolish(self.state_label)
            self.state_label.style().polish(self.state_label)
