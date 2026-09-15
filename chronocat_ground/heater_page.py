from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .protocol import HEATER_MANUAL_MAX_DUTY_PERMILLE, CommandResponse, status_name


HEATER_COUNT = 12


class HeaterControlPage(QWidget):
    manual_duty_requested = Signal(int, int)
    all_on_requested = Signal(int)
    all_off_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self._connected = False
        self._busy = False
        self.rows: list[dict[str, QWidget]] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("panel")
        header_layout = QVBoxLayout(header)
        title = QLabel("HEATER TESTING")
        title.setObjectName("panelTitle")
        header_layout.addWidget(title)
        note = QLabel(
            "Direct manual PWM control for bench testing. Each heater is independent; "
            "temperature sensors and PID are not required for this page."
        )
        note.setObjectName("smallNote")
        note.setWordWrap(True)
        header_layout.addWidget(note)
        root.addWidget(header)

        bulk = QFrame()
        bulk.setObjectName("panel")
        bulk_layout = QHBoxLayout(bulk)
        bulk_layout.addWidget(QLabel("Uniform duty for all heaters"))
        self.bulk_duty = QSpinBox()
        self.bulk_duty.setRange(0, HEATER_MANUAL_MAX_DUTY_PERMILLE)
        self.bulk_duty.setSuffix(" / 1000")
        self.bulk_duty.setValue(2)
        bulk_layout.addWidget(self.bulk_duty)
        self.all_on_button = QPushButton("Turn All On")
        self.all_on_button.clicked.connect(self._request_all_on)
        bulk_layout.addWidget(self.all_on_button)
        self.all_off_button = QPushButton("Turn All Off")
        self.all_off_button.clicked.connect(self._request_all_off)
        bulk_layout.addWidget(self.all_off_button)
        self.bulk_status = QLabel("All heaters off / not confirmed")
        self.bulk_status.setObjectName("smallNote")
        bulk_layout.addWidget(self.bulk_status, 1)
        root.addWidget(bulk)

        rows_panel = QFrame()
        rows_panel.setObjectName("panel")
        rows_layout = QVBoxLayout(rows_panel)
        rows_layout.addWidget(QLabel("INDIVIDUAL HEATERS"), 0)

        header_grid = QGridLayout()
        header_grid.setColumnStretch(3, 1)
        for column, text in enumerate(("Heater", "Duty", "Controls", "Confirmed state")):
            label = QLabel(text)
            label.setObjectName("smallNote")
            header_grid.addWidget(label, 0, column)
        rows_layout.addLayout(header_grid)

        rows_container = QWidget()
        rows_grid = QGridLayout(rows_container)
        rows_grid.setContentsMargins(0, 0, 0, 0)
        rows_grid.setVerticalSpacing(6)
        rows_grid.setColumnStretch(3, 1)

        for heater_id in range(HEATER_COUNT):
            label = QLabel(f"H{heater_id}")
            label.setObjectName("kpiLabel")
            duty = QSpinBox()
            duty.setRange(0, HEATER_MANUAL_MAX_DUTY_PERMILLE)
            duty.setSuffix(" / 1000")
            duty.setValue(2)
            on_button = QPushButton("ON")
            off_button = QPushButton("OFF")
            status = QLabel("OFF / not confirmed")
            status.setObjectName("smallNote")
            status.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

            on_button.clicked.connect(
                lambda checked=False, heater_id=heater_id: self._request_on(heater_id)
            )
            off_button.clicked.connect(
                lambda checked=False, heater_id=heater_id: self._request_off(heater_id)
            )

            controls = QWidget()
            controls_layout = QHBoxLayout(controls)
            controls_layout.setContentsMargins(0, 0, 0, 0)
            controls_layout.addWidget(on_button)
            controls_layout.addWidget(off_button)

            row = {"duty": duty, "on": on_button, "off": off_button, "status": status}
            self.rows.append(row)
            row_number = heater_id + 1
            rows_grid.addWidget(label, row_number, 0)
            rows_grid.addWidget(duty, row_number, 1)
            rows_grid.addWidget(controls, row_number, 2)
            rows_grid.addWidget(status, row_number, 3)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(rows_container)
        rows_layout.addWidget(scroll, 1)
        root.addWidget(rows_panel, 1)

        self.set_connected(False)

    def _request_on(self, heater_id: int) -> None:
        duty = self.rows[heater_id]["duty"]
        assert isinstance(duty, QSpinBox)
        value = duty.value()
        if value == 0:
            self._set_row_status(heater_id, "Choose a nonzero duty")
            return
        self.manual_duty_requested.emit(heater_id, value)

    def _request_off(self, heater_id: int) -> None:
        self.manual_duty_requested.emit(heater_id, 0)

    def _request_all_on(self) -> None:
        value = self.bulk_duty.value()
        if value == 0:
            self.bulk_status.setText("Choose a nonzero duty")
            return
        self.all_on_requested.emit(value)

    def _request_all_off(self) -> None:
        self.all_off_requested.emit()

    def set_connected(self, connected: bool) -> None:
        self._connected = connected
        if not connected:
            self.bulk_status.setText("Disconnected / state not confirmed")
            for heater_id in range(HEATER_COUNT):
                self._set_row_status(heater_id, "UNKNOWN / disconnected")
        self._update_enabled()

    def set_command_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_enabled()

    def _update_enabled(self) -> None:
        available = self._connected and not self._busy
        self.bulk_duty.setEnabled(available)
        self.all_on_button.setEnabled(available)
        self.all_off_button.setEnabled(available)
        for row in self.rows:
            for key in ("duty", "on", "off"):
                row[key].setEnabled(available)

    def set_row_pending(self, heater_id: int, duty: int) -> None:
        self._set_row_status(heater_id, f"sending {duty}/1000...")

    def set_bulk_pending(self, duty: int | None) -> None:
        self.bulk_status.setText(
            "turning all heaters off..." if duty is None else f"turning all heaters on at {duty}/1000..."
        )

    def apply_manual_response(self, heater_id: int, response: CommandResponse) -> None:
        if response.ok:
            self._set_row_status(heater_id, f"{'ON' if response.arg2 else 'OFF'} / {response.arg2}/1000")
        else:
            self._set_row_status(heater_id, f"rejected: {status_name(response.status)}")

    def apply_bulk_response(self, duty: int | None, response: CommandResponse) -> None:
        if not response.ok:
            self.bulk_status.setText(f"rejected: {status_name(response.status)}")
            return
        if duty is None:
            self.bulk_status.setText("All heaters OFF / confirmed")
            for heater_id in range(HEATER_COUNT):
                self._set_row_status(heater_id, "OFF / 0/1000")
        else:
            applied = response.arg2
            self.bulk_status.setText(f"All heaters ON / {applied}/1000 confirmed")
            for heater_id in range(HEATER_COUNT):
                self._set_row_status(heater_id, f"ON / {applied}/1000")

    def set_command_error(self, heater_id: int | None, message: str) -> None:
        if heater_id is None:
            self.bulk_status.setText(f"failed: {message}")
        else:
            self._set_row_status(heater_id, f"failed: {message}")

    def _set_row_status(self, heater_id: int, text: str) -> None:
        self.rows[heater_id]["status"].setText(text)
