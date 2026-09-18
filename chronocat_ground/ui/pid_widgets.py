from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel

from ..heater_safety import HEATER_SENSOR_IDS
from ..protocol import HeaterPidReading


SENSOR_NAMES = (
    "U3",
    "U4",
    "U5",
    "U0",
    "U1",
    "U2",
    "F2_U3",
    "F2_U4",
    "F2_U5",
    "F2_U0",
    "F2_U1",
    "F2_U2",
)


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

        self.duty_label = QLabel("0‰")
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
            self.duty_label.setText("0‰")
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

        if not reading.sensor_mapped:
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
        self.duty_label.setText(f"{reading.duty_permille}‰")
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
