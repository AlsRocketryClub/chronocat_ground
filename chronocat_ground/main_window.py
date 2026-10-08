from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import math
import sqlite3
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QPushButton,
)

from .command_client import CommandClient
from .command_dispatcher import CommandDispatcher, CommandRequest
from .protocol import (
    COMMAND_GEIGER_CLEAR_HISTORY,
    COMMAND_GEIGER_READ_XDER,
    COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
    COMMAND_GEIGER_RESET_STATS,
    COMMAND_HEATER_SET_KP,
    COMMAND_HEATER_SET_KD,
    COMMAND_HEATER_SET_KI,
    COMMAND_HEATER_SET_TARGET,
    COMMAND_HEATER_SET_TARGET_SIGNED,
    COMMAND_HEATER_SET_MANUAL_DUTY,
    COMMAND_HEATER_FORCE_DUTY,
    STATUS_BAD_VALUE,
    TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET,
    COMMAND_HEATER_RETURN_TO_PID,
    COMMAND_HEATER_ALL_OFF,
    DEFAULT_TELEMETRY_PORT,
    TEMP_SENSOR_LABELS,
    CommandResponse,
    CombinedTelemetryPacket,
    PidTelemetryPacket,
    TelemetryPacket,
    ad7177_status_names,
    command_name,
    decode_heater_gain,
    decode_float32_args,
    decode_heater_target_c,
    decode_heater_target_signed_c,
    encode_heater_gain,
    encode_heater_target_c,
    encode_heater_target_signed_c,
    geiger_reset_actions_name,
    status_name,
    TELEMETRY_FLAG_SD_LOG_ACTIVE,
    TELEMETRY_FLAG_SD_LOG_ERROR,
    telemetry_value_name,
)
from .pid_page import PidPage
from .reset_monitor import ResetMonitor, same_reset
from .pid_profiles import PidProfile, profile_by_name
from .telemetry_csv import CSV_MODE_FULL, TelemetryCsvLogger
from .telemetry_db import DEFAULT_DATABASE_PATH, TelemetryDb
from .health_model import (
    HealthEventLog,
    PacketLossTracker,
    active_issues,
    evaluate_health,
    format_uptime,
    link_status,
)
from .dosimetry import SessionDose, is_plausible_xder
from .sample_layout import SAMPLE_CHANNELS
from .telemetry_history import TelemetryHistory, adc_point_for_mode
from .telemetry_receiver import TelemetryReceiver
from .ui.widgets import SampleCard
from .ui.styles import APPLICATION_STYLE
from .ui.main_window_pages import MainWindowPagesMixin


VIEW_DASHBOARD = "DASHBOARD"
VIEW_RADIATION = "RADIATION"
VIEW_SAMPLES = "SAMPLES"
VIEW_TEMPERATURE = "HEATING"
VIEW_HEALTH = "HEALTH"
VIEW_SETTINGS = "SETTINGS"


@dataclass
class PendingHeaterCommand:
    """Presentation context for one in-flight heater operation."""

    command: int
    param_name: str
    encoded_value: int
    value_kind: str
    expected_arg1: int = 0
    heater_id: int | None = None


@dataclass
class PendingPidOperation:
    heater_ids: tuple[int, ...]
    target: float
    profile: PidProfile
    heater_index: int = 0
    step: int = 0


class MainWindow(MainWindowPagesMixin, QMainWindow):
    def __init__(self, database_path: str | Path = DEFAULT_DATABASE_PATH) -> None:
        super().__init__()
        self.setWindowTitle("CHRONO-CAT Ground Station")
        self.resize(1180, 760)
        self.setMinimumSize(760, 520)
        self._closing = False

        self.client = CommandClient()
        self.command_dispatcher = CommandDispatcher(self.client, self)
        self.command_dispatcher.completed.connect(self.command_completed)
        self.command_dispatcher.failed.connect(self.command_failed)
        self.command_dispatcher.connection_succeeded.connect(self.connection_succeeded)
        self.command_dispatcher.connection_failed.connect(self.connection_failed)
        self.command_dispatcher.busy_changed.connect(self._on_command_busy_changed)
        self.connection_pending = False
        self.last_telemetry_time: float | None = None
        self.view_buttons: dict[str, list[QPushButton]] = {}
        self.database_path = Path(database_path)
        self.adc_db = TelemetryDb(self.database_path, async_writes=True)
        self.telemetry_history = TelemetryHistory(self.adc_db)
        self.sample_cards: list[SampleCard] = []
        self.samples_display_mode = "voltage"
        self._last_adc_packet: TelemetryPacket | None = None
        self._last_adc_history = None
        self.plot_dialogs: dict[str, QDialog] = {}
        self.plot_dialog_refs: dict[str, dict[str, object]] = {}
        self.csv_logger: TelemetryCsvLogger | None = None
        self.pending_heater_command: PendingHeaterCommand | None = None
        self.pending_pid_operation: PendingPidOperation | None = None
        self.pid_page: PidPage | None = None
        self.geiger_xder_values: dict[int, float | None] = {0: None, 1: None}
        self.geiger_xder_read_wall: dict[int, float | None] = {0: None, 1: None}
        self._xder_reads_pending: list[int] = []
        self.session_dose = SessionDose()
        self.health_events = HealthEventLog()
        self.reset_monitor = ResetMonitor()
        self._health_packet: TelemetryPacket | None = None
        self._health_pid: PidTelemetryPacket | None = None
        self._receiver_error = ""
        self._health_items: tuple = ()
        self.packet_loss = PacketLossTracker()

        self.telemetry_receiver = TelemetryReceiver(DEFAULT_TELEMETRY_PORT)
        self.telemetry_receiver.packet_received.connect(self.on_telemetry_packet)
        self.telemetry_receiver.receive_error.connect(self.on_receiver_error)

        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_telemetry_age)
        self.age_timer.start(1000)

        self.apply_style()
        self.setCentralWidget(self.build_ui())
        self.fit_action_buttons()
        self.apply_saved_navigation()
        self._load_stored_xder()

        self.switch_view(VIEW_DASHBOARD)
        self.update_connection_state()
        self.telemetry_receiver.start()
        self.log(f"Listening for UDP telemetry on port {DEFAULT_TELEMETRY_PORT}")

    def apply_style(self) -> None:
        QApplication.instance().setStyleSheet(APPLICATION_STYLE)

    def toggle_connection(self) -> None:
        if self.client.connected:
            self.client.disconnect()
            self.log("Disconnected from command server")
            self.update_connection_state()
            return

        if self.connection_pending:
            return

        host = self.host_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.log("Connect failed: port must be a number from 1 to 65535")
            return

        self.connection_pending = True
        self.log(f"Connecting to {host}:{port}...")
        self.command_dispatcher.connect(host, port)
        self.update_connection_state()

    def connection_succeeded(self, host: str, port: int) -> None:
        self.connection_pending = False
        self.log(f"Connected to {host}:{port}")
        self.update_connection_state()
        # Coefficients only need reading once; fetch any not yet stored.
        self._xder_reads_pending = [
            detector_id for detector_id, value in self.geiger_xder_values.items() if value is None
        ]
        QTimer.singleShot(0, self._read_next_pending_xder)

    def connection_failed(self, message: str) -> None:
        self.connection_pending = False
        self.log(f"Connect failed: {message}")
        self.log("Check that the board is flashed, linked, and reachable at this IP.")
        self.update_connection_state()

    def toggle_csv_logging(self) -> None:
        if self.csv_logger is not None and self.csv_logger.active:
            self.stop_csv_logging()
            return

        self.csv_logger = TelemetryCsvLogger(mode=CSV_MODE_FULL, background_sync=True)
        try:
            self.csv_logger.start()
        except OSError as exc:
            self.csv_logger = None
            self.csv_log_status.setText("CSV logging failed")
            self.log(f"CSV logging failed: {exc}")
            return

        self.csv_log_button.setText("Stop CSV logging")
        self.csv_log_status.setText("CSV on (0)")
        self.csv_log_status.setToolTip(str(self.csv_logger.path))
        self.log(f"CSV logging started: {self.csv_logger.path}")

    def stop_csv_logging(self) -> None:
        if self.csv_logger is None:
            return
        path = self.csv_logger.path
        packet_count = self.csv_logger.packet_count
        self.csv_logger.stop()
        self.csv_logger = None
        self.csv_log_button.setText("Start CSV logging")
        self.csv_log_status.setText(f"CSV off ({packet_count} saved)")
        self.log(f"CSV logging stopped: {path} ({packet_count} packet(s))")

    def update_connection_state(self) -> None:
        connected = self.client.connected
        command_in_progress = self.command_dispatcher.busy
        connection_status = "CONNECTING" if self.connection_pending else (
            "CONNECTED" if connected else "DISCONNECTED"
        )
        connection_state = "connecting" if self.connection_pending else (
            "connected" if connected else "disconnected"
        )
        self.connection_indicator.set_status(connection_status, connection_state)
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(not command_in_progress)
        self.host_input.setEnabled(not command_in_progress)
        self.port_input.setEnabled(not command_in_progress)

        self.refresh_health()

        for button in (
            self.ping_button,
            self.status_button,
            self.telemetry_on_button,
            self.telemetry_off_button,
            self.geiger_reset_dose_button,
            self.geiger_clear_history_button,
            self.geiger_reset_stats_button,
        ):
            button.setEnabled(connected and not command_in_progress)

        if self.pid_page is not None:
            self.pid_page.set_connected(connected)
            self.pid_page.set_command_busy(command_in_progress)

    def _on_command_busy_changed(self, _busy: bool) -> None:
        self.update_connection_state()

    def send_command(
        self,
        command: int,
        value: int,
        arg2: int = 0,
        timeout: float | None = None,
    ) -> None:
        if self.command_dispatcher.busy:
            self.log("Command already in progress")
            return

        self.command_dispatcher.submit(CommandRequest(command, value, arg2, timeout))

    def command_completed(self, request: CommandRequest, response: CommandResponse) -> None:
        self.update_connection_state()

        if request.command == COMMAND_GEIGER_READ_XDER:
            detector_id = request.arg1
            if response.status != 0:
                self._xder_read_failed(detector_id, status_name(response.status))
            else:
                try:
                    value = decode_float32_args(response.arg1, response.arg2)
                except ValueError as exc:
                    self._xder_read_failed(detector_id, f"invalid value ({exc})")
                else:
                    if is_plausible_xder(value):
                        self._store_xder(detector_id, value, time.time())
                        self.log(f"Geiger {detector_id + 1} xDER: {value:.9g} Sv/h per cps (stored)")
                    else:
                        self._xder_read_failed(detector_id, f"unusable value {value:.9g}")
            self.log_response(response)
            QTimer.singleShot(0, self._read_next_pending_xder)
            return

        if (
            self.pending_heater_command is not None
            and response.command == self.pending_heater_command.command
        ):
            pending = self.pending_heater_command
            param_name = pending.param_name
            encoded_value = pending.encoded_value
            value_kind = pending.value_kind
            expected_arg1 = pending.expected_arg1
            heater_id = pending.heater_id
            self.pending_heater_command = None

            if value_kind == "bulk_pid":
                operation = self.pending_pid_operation
                response_matches = response.status == 0 and response.arg1 == expected_arg1
                if response_matches and operation is not None and operation.step < 4:
                    response_matches = response.arg2 == encoded_value
                if response_matches:
                    self.log_response(response)
                    self._continue_pid_operation()
                else:
                    self._fail_pid_operation(
                        f"H{heater_id} {param_name} rejected: {status_name(response.status)}"
                    )
                    self.log_response(response)
                return

            if response.status == 0 and response.arg1 == expected_arg1:
                if value_kind == "bulk_off" and self.pid_page is not None:
                    self.pid_page.set_command_status("all mapped heaters off confirmed")
                elif value_kind == "target" and self.pid_page is not None:
                    decoded = decode_heater_target_c(response.arg2)
                    if response.arg2 != encoded_value:
                        self.pid_page.set_command_status(
                            f"applied {decoded:.3f} C "
                            f"(requested {decode_heater_target_c(encoded_value):.3f} C)"
                        )
                    else:
                        self.pid_page.set_command_status(f"applied {decoded:.3f} C")
                elif value_kind == "signed_target" and self.pid_page is not None:
                    decoded = decode_heater_target_signed_c(response.arg2)
                    requested = decode_heater_target_signed_c(encoded_value)
                    self.pid_page.set_command_status(
                        f"applied {decoded:.1f} C"
                        + (f" (requested {requested:.1f} C)" if decoded != requested else "")
                    )
                elif value_kind == "gain" and self.pid_page is not None:
                    decoded = decode_heater_gain(response.arg2)
                    if response.arg2 != encoded_value:
                        self.pid_page.set_command_status(
                            f"applied {decoded:.3f} "
                            f"(requested {decode_heater_gain(encoded_value):.3f})"
                        )
                    else:
                        self.pid_page.set_command_status(f"applied {decoded:.3f}")
                elif value_kind == "forced_duty" and self.pid_page is not None:
                    self.pid_page.set_command_status(
                        f"FORCED duty active: {response.arg2 / 10.0:.1f}%, sensor protection off"
                    )
                elif value_kind == "duty" and self.pid_page is not None:
                    self.pid_page.set_command_status(
                        f"manual duty active: {response.arg2 / 10.0:.1f}%"
                    )
                elif self.pid_page is not None:
                    self.pid_page.set_command_status("PID mode active")
                self.log_response(response)
            else:
                if self.pid_page is not None:
                    self.pid_page.set_command_status(f"rejected: {status_name(response.status)}")
                self.log(f"Heater {param_name} rejected: {status_name(response.status)}")
                if (
                    value_kind == "duty"
                    and encoded_value > 0
                    and response.status == STATUS_BAD_VALUE
                    and self.pid_page is not None
                ):
                    # Out of the reply handler first, so the dialog never blocks it.
                    QTimer.singleShot(
                        0, lambda: self.pid_page.offer_force_override(heater_id, encoded_value)
                    )
        else:
            self.pending_heater_command = None
            self.log_response(response)

    def command_failed(self, request: CommandRequest, message: str) -> None:
        if request.command == COMMAND_GEIGER_READ_XDER:
            self._xder_read_failed(request.arg1, message)
            QTimer.singleShot(0, self._read_next_pending_xder)
        if self.pending_pid_operation is not None:
            self._fail_pid_operation(f"PID operation failed: {message}", disconnect=True)
            return
        if self.pending_heater_command is not None:
            pending = self.pending_heater_command
            param_name = pending.param_name
            heater_id = pending.heater_id
            self.pending_heater_command = None
            if self.pid_page is not None:
                self.pid_page.set_command_status(f"failed: {message}")
            self.log(f"Heater {param_name} command failed: {message}")
        else:
            self.log(f"Command failed: {message}")
        self.client.disconnect()
        self.update_connection_state()

    def read_geiger_xder(self, detector_id: int) -> None:
        if detector_id not in self.geiger_xder_values:
            return
        self.log(f"Reading Geiger {detector_id + 1} xDER")
        self.send_command(COMMAND_GEIGER_READ_XDER, detector_id, 0, timeout=2.0)

    def set_geiger_xder_display(self, detector_id: int, value: str) -> None:
        label = self.geiger_xder_labels.get(detector_id)
        if label is not None:
            label.setText(value)

    def _load_stored_xder(self) -> None:
        """Use coefficients read in an earlier session; they never change."""
        for detector_id, (value, read_wall) in self.adc_db.load_xder().items():
            if detector_id in self.geiger_xder_values and is_plausible_xder(value):
                self._store_xder(detector_id, value, read_wall, persist=False)

    def _store_xder(self, detector_id: int, value: float, read_wall: float, persist: bool = True) -> None:
        self.geiger_xder_values[detector_id] = value
        self.geiger_xder_read_wall[detector_id] = read_wall
        if persist:
            self.adc_db.save_xder(detector_id, value, read_wall)
        read_at = datetime.fromtimestamp(read_wall).strftime("%Y-%m-%d %H:%M")
        self.set_geiger_xder_display(detector_id, f"{value:.6g} Sv/h per cps  (read {read_at})")
        self._update_dose_units()

    def _xder_read_failed(self, detector_id: int, reason: str) -> None:
        """A failed read keeps a known coefficient; it only reports the failure."""
        self.log(f"Geiger {detector_id + 1} xDER read failed: {reason}")
        if self.geiger_xder_values.get(detector_id) is None:
            self.set_geiger_xder_display(detector_id, f"not read ({reason})")

    def _read_next_pending_xder(self) -> None:
        if self._xder_reads_pending and not self.command_dispatcher.busy and self.client.connected:
            self.read_geiger_xder(self._xder_reads_pending.pop(0))

    def _update_dose_units(self) -> None:
        self.radiation.set_xder(dict(self.geiger_xder_values))
        self.dashboard.set_xder(dict(self.geiger_xder_values))

    def send_geiger_command(self, command: int, action: str, timeout: float | None = None) -> None:
        self.log(f"Sending Geiger command: {action}")
        self.send_command(command, 0, 0, timeout=timeout)

    def turn_all_heaters_off(self) -> None:
        if not self.client.connected or self.command_dispatcher.busy:
            return
        self.pending_heater_command = PendingHeaterCommand(
            command=COMMAND_HEATER_ALL_OFF,
            param_name="all heaters off",
            encoded_value=0,
            value_kind="bulk_off",
        )
        if self.pid_page is not None:
            self.pid_page.set_command_status("sending all-off...")
        self.log("Turning all heaters off")
        self.send_command(COMMAND_HEATER_ALL_OFF, 0, 0)

    def set_all_pid(self, activate: bool, target: float, profile_name: str) -> None:
        if not self.client.connected or self.command_dispatcher.busy or self.pid_page is None:
            return
        if not activate:
            self.turn_all_heaters_off()
            return

        heater_ids = self.pid_page.mapped_heater_ids()
        if not heater_ids:
            self.pid_page.set_command_status("cannot activate PID: no mapped heaters")
            return
        operation = PendingPidOperation(
            heater_ids=tuple(heater_ids),
            target=target,
            profile=profile_by_name(profile_name),
        )
        self.pending_pid_operation = operation
        self.pid_page.set_global_operation_busy(True)
        self.pid_page.set_command_status(
            f"applying {operation.profile.name} to {len(operation.heater_ids)} mapped heater(s)..."
        )
        self._send_next_pid_operation_step()

    def _send_next_pid_operation_step(self) -> None:
        operation = self.pending_pid_operation
        if operation is None or self.pid_page is None:
            return
        if operation.heater_index >= len(operation.heater_ids):
            self.pending_pid_operation = None
            self.pid_page.set_global_operation_busy(False)
            self.pid_page.set_command_status("all mapped heaters PID active")
            return

        heater_id = operation.heater_ids[operation.heater_index]
        gains = operation.profile.gains_for(heater_id)
        steps = (
            self._target_command(operation.target),
            (COMMAND_HEATER_SET_KP, "kp", encode_heater_gain(gains.kp)),
            (COMMAND_HEATER_SET_KI, "ki", encode_heater_gain(gains.ki)),
            (COMMAND_HEATER_SET_KD, "kd", encode_heater_gain(gains.kd)),
            (COMMAND_HEATER_RETURN_TO_PID, "PID mode", 0),
        )
        command, name, encoded_value = steps[operation.step]
        self.pending_heater_command = PendingHeaterCommand(
            command=command,
            param_name=f"H{heater_id} {name}",
            encoded_value=encoded_value,
            value_kind="bulk_pid",
            expected_arg1=heater_id,
            heater_id=heater_id,
        )
        self.send_command(command, heater_id, encoded_value)

    def _continue_pid_operation(self) -> None:
        operation = self.pending_pid_operation
        if operation is None:
            return
        operation.step += 1
        if operation.step == 5:
            operation.step = 0
            operation.heater_index += 1
        self._send_next_pid_operation_step()

    def _fail_pid_operation(self, message: str, disconnect: bool = False) -> None:
        self.pending_heater_command = None
        self.pending_pid_operation = None
        if self.pid_page is not None:
            self.pid_page.set_global_operation_busy(False)
            self.pid_page.set_command_status(message)
        self.log(message)
        if disconnect:
            self.client.disconnect()
            self.update_connection_state()
        elif self.client.connected and not self.command_dispatcher.busy:
            self.turn_all_heaters_off()

    def set_pid_target(self, heater_id: int, value: float) -> None:
        if self.pid_page is None:
            return
        self.pid_page.set_command_status("sending target...")
        command, name, encoded = self._target_command(value)
        self.send_heater_parameter(
            command,
            f"H{heater_id} target",
            encoded,
            name,
            heater_id,
        )

    @staticmethod
    def _target_command(value: float) -> tuple[int, str, int]:
        if value < 0:
            return (COMMAND_HEATER_SET_TARGET_SIGNED, "signed_target", encode_heater_target_signed_c(value))
        return (COMMAND_HEATER_SET_TARGET, "target", encode_heater_target_c(value))

    def set_pid_gain(self, heater_id: int, name: str, value: float) -> None:
        if self.pid_page is None:
            return
        command = {
            "kp": COMMAND_HEATER_SET_KP,
            "ki": COMMAND_HEATER_SET_KI,
            "kd": COMMAND_HEATER_SET_KD,
        }[name]
        self.pid_page.set_command_status(f"sending {name}...")
        self.send_heater_parameter(
            command,
            f"H{heater_id} {name}",
            encode_heater_gain(value),
            "gain",
            heater_id,
        )

    def set_pid_manual_duty(self, heater_id: int, duty_permille: int) -> None:
        if self.pid_page is None:
            return
        self.pid_page.set_command_status("sending manual duty...")
        self.send_heater_parameter(
            COMMAND_HEATER_SET_MANUAL_DUTY,
            f"H{heater_id} manual duty",
            duty_permille,
            "duty",
            heater_id,
        )

    def set_pid_force_duty(self, heater_id: int, duty_permille: int) -> None:
        if self.pid_page is None:
            return
        self.log(f"H{heater_id}: forcing {duty_permille / 10.0:.1f}% with sensor protection bypassed")
        self.send_heater_parameter(
            COMMAND_HEATER_FORCE_DUTY,
            f"H{heater_id} forced duty",
            duty_permille,
            "forced_duty",
            heater_id,
        )

    def return_pid_heater(self, heater_id: int) -> None:
        if self.pid_page is None:
            return
        self.pid_page.set_command_status("returning to PID...")
        self.send_heater_parameter(
            COMMAND_HEATER_RETURN_TO_PID,
            f"H{heater_id} PID mode",
            0,
            "pid",
            heater_id,
        )

    def send_heater_parameter(
        self,
        command: int,
        param_name: str,
        encoded_value: int,
        value_kind: str = "gain",
        heater_id: int = 0,
    ) -> None:
        if not self.client.connected:
            self.log(f"Cannot set heater {param_name}: not connected")
            return

        self.pending_heater_command = PendingHeaterCommand(
            command=command,
            param_name=param_name,
            encoded_value=encoded_value,
            value_kind=value_kind,
            expected_arg1=heater_id,
            heater_id=heater_id,
        )
        if self.pid_page is not None:
            self.pid_page.set_command_status("sending...")
        self.log(f"Sending heater {param_name} command (value={encoded_value})")
        self.send_command(command, heater_id, encoded_value)

    def log_response(self, response: CommandResponse) -> None:
        if response.command in (
            COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
            COMMAND_GEIGER_CLEAR_HISTORY,
            COMMAND_GEIGER_RESET_STATS,
        ):
            text = (
                f"Response {status_name(response.status)} "
                f"command={command_name(response.command)} "
                f"detector_errors=0x{response.arg1:04x} "
                f"actions={geiger_reset_actions_name(response.arg2)}"
            )
        else:
            text = (
                f"Response {status_name(response.status)} "
                f"command={command_name(response.command)} "
                f"arg1={telemetry_value_name(response.arg1)} arg2={response.arg2}"
            )
        self.log(text)

    def on_telemetry_packet(
        self,
        packet: TelemetryPacket | PidTelemetryPacket | CombinedTelemetryPacket,
        source: str,
        received_monotonic: float | None = None,
        received_wall: float | None = None,
    ) -> None:
        if self._closing:
            return
        received_monotonic = time.monotonic() if received_monotonic is None else received_monotonic
        received_wall = time.time() if received_wall is None else received_wall
        combined_packet: CombinedTelemetryPacket | None = None
        if isinstance(packet, CombinedTelemetryPacket):
            combined_packet = packet
            self.last_telemetry_time = received_monotonic
            self.update_sd_log_status(packet.flags)
            self.set_telemetry_status("receiving")
            if self.pid_page is not None:
                self.pid_page.update_packet(packet.pid, received_monotonic)
                self.pid_page.update_ambient(packet.standard, received_monotonic)
            packet = packet.standard
        elif isinstance(packet, PidTelemetryPacket):
            self.last_telemetry_time = received_monotonic
            self.update_sd_log_status(packet.flags)
            self.set_telemetry_status("receiving")
            if self.pid_page is not None:
                self.pid_page.update_packet(packet, received_monotonic)
            if self.csv_logger is not None and self.csv_logger.active:
                try:
                    self.csv_logger.write_packet(
                        packet, source, datetime.fromtimestamp(received_wall)
                    )
                except OSError as exc:
                    self.log(f"PID CSV logging failed: {exc}")
                    self.stop_csv_logging()
                else:
                    self.csv_log_status.setText(f"CSV on ({self.csv_logger.packet_count})")
            return

        self.last_telemetry_time = received_monotonic
        history = self.telemetry_history.record(
            packet, received_monotonic, received_wall,
            combined_packet.pid if combined_packet is not None else None,
        )
        if self.telemetry_history.database_error is not None:
            self.log(
                f"Telemetry database logging failed: "
                f"{self.telemetry_history.database_error}"
            )
            self.telemetry_history.database_error = None
        self.update_sd_log_status(packet.flags)
        self.set_telemetry_status("receiving")

        self.packet_loss.record(packet.counter)
        for detector_id in range(2):
            reading = packet.geiger_reading(detector_id)
            self.session_dose.record(
                detector_id,
                received_monotonic,
                reading.dose_rate_cps if reading is not None and reading.valid else None,
                self.geiger_xder_values.get(detector_id),
            )
        self._last_adc_packet = packet
        self.board_map.set_temperatures(packet)
        self._last_adc_history = history
        self.refresh_sample_cards()
        self.update_plot_dialogs()
        self.diagnostics.show_packet(
            packet, combined_packet.pid if combined_packet is not None else None, source
        )
        self._health_packet = packet
        if combined_packet is not None:
            self._health_pid = combined_packet.pid
        self._check_board_reset(packet, received_wall)
        self._receiver_error = ""
        self.refresh_health(uptime_ms=packet.timestamp)
        self.dashboard.show_packet(packet, self._health_pid, history, self._health_items)
        self.radiation.show_packet(packet, history, self._health_items, self.session_dose)

        if self.csv_logger is not None and self.csv_logger.active:
            try:
                self.csv_logger.write_packet(
                    combined_packet or packet,
                    source,
                    datetime.fromtimestamp(received_wall),
                )
            except OSError as exc:
                self.log(f"CSV logging failed: {exc}")
                self.stop_csv_logging()
            else:
                self.csv_log_status.setText(f"CSV on ({self.csv_logger.packet_count})")

    def _check_board_reset(self, packet: TelemetryPacket, received_wall: float) -> None:
        pid = self._health_pid
        heaters = (
            tuple(heater_id for heater_id, reading in enumerate(pid.heaters) if reading.pid_enabled)
            if pid is not None else None
        )
        reset = self.reset_monitor.update(
            packet.timestamp,
            bool(packet.flags & TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET),
            received_wall,
            heaters,
        )
        if reset is None or same_reset(reset.reset_wall, self.dismissed_reset_wall()):
            return
        self._record_event(
            "reset",
            f"{reset.cause} reset at {datetime.fromtimestamp(reset.reset_wall):%Y-%m-%d %H:%M:%S}"
            + (f", previous uptime {reset.previous_uptime_ms} ms" if reset.previous_uptime_ms is not None else ""),
        )
        self.log(f"Board reset detected ({reset.cause})")
        self.reset_banner.add_reset(reset)

    def update_sd_log_status(self, flags: int) -> None:
        if (flags & TELEMETRY_FLAG_SD_LOG_ERROR) != 0:
            text = "ERROR"
            state = "error"
            tooltip = "Firmware SD temperature logging has failed"
        elif (flags & TELEMETRY_FLAG_SD_LOG_ACTIVE) != 0:
            text = "LOGGING"
            state = "active"
            tooltip = "Firmware SD temperature logging is active"
        else:
            text = "OFF"
            state = "off"
            tooltip = "Firmware SD temperature logging is not active"
        self.sd_log_indicator.set_status(text, state, tooltip)

    def set_telemetry_status(self, status: str) -> None:
        labels = {
            "waiting": ("WAITING", "No telemetry received"),
            "receiving": ("RECEIVING", "Telemetry is arriving"),
            "stale": ("STALE", "No telemetry packet has arrived recently"),
            "error": ("ERROR", "Downlink reported an error"),
        }
        text, tooltip = labels.get(status, labels["error"])
        self.telemetry_indicator.set_status(text, status, tooltip)

    def set_samples_display_mode(self, mode: str) -> None:
        self.samples_display_mode = mode
        for button_mode, button in self.samples_unit_buttons.items():
            button.setChecked(button_mode == mode)
        self.refresh_sample_cards()

    def refresh_sample_cards(self) -> None:
        packet = self._last_adc_packet
        history = self._last_adc_history
        if packet is None or history is None:
            return
        mode = self.samples_display_mode
        axis_label, axis_hover = ("Raw24", "Raw24") if mode == "raw" else ("Volts (V)", "Volts")
        # Rebuilding twelve plots is the expensive part, so it waits until the
        # Samples page is shown; switch_view refreshes it on arrival.
        if self.pages.currentIndex() != self.samples_page_index:
            return
        values: list[float] = []
        for reading in packet.ad7177_readings:
            channel = SAMPLE_CHANNELS.get(reading.slot)
            if channel is None:
                continue
            valid = packet.os_adc_valid(reading.slot)
            value_text = (
                f"0x{reading.raw24:06x}" if mode == "raw" else f"{reading.voltage:.6f} V"
            )
            card = self.sample_cards[reading.slot]
            card.set_value_axis(axis_label, axis_hover)
            points = [adc_point_for_mode(entry, mode) for entry in history.adc_points[reading.slot]]
            values.extend(point[2] for point in points if math.isfinite(point[2]))
            card.set_points(points)
            card.set_reading(
                value_text,
                f"0x{reading.status:02x} ({ad7177_status_names(reading.status)})",
                valid,
            )
            card.set_temperatures(
                [
                    (TEMP_SENSOR_LABELS[sensor_id], packet.temperature_c(sensor_id))
                    for sensor_id in channel.temperature_sensor_ids
                ]
            )
        # "Same scale": every plot spans the lowest to highest value of all channels.
        shared = (
            (min(values), max(values))
            if self.samples_same_scale_button.isChecked() and values
            else None
        )
        for card in self.sample_cards:
            card.plot.set_shared_y_extent(shared)

    def _mark_downlink_derived_status_unknown(self) -> None:
        """Data that only arrives over a stale downlink can't be trusted; say so."""
        self.sd_log_indicator.set_status(
            "UNKNOWN", "unknown", "Downlink is stale; SD logging state can't be confirmed"
        )

    def update_telemetry_age(self) -> None:
        if self.client.connected and not self.client.check_connection():
            self.update_connection_state()
            self.log("Connection lost")

        if self.last_telemetry_time is None:
            self.set_telemetry_status("waiting")
            self._mark_downlink_derived_status_unknown()
            self.refresh_health()
            return

        age = time.monotonic() - self.last_telemetry_time
        age_text = f"{age:.1f}s ago"
        self.diagnostics.show_age(age_text)
        if age >= 2.5:
            self._mark_downlink_derived_status_unknown()
        self.refresh_health()

        active = age < 2.5
        self.set_telemetry_status("receiving" if active else "stale")
    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")
        self._record_event("log", message)

    def _record_event(self, kind: str, message: str) -> None:
        """Keep the operator's record in the database; the log panel is lost on close."""
        database = getattr(self, "adc_db", None)
        if database is None:
            return
        try:
            database.insert_event(time.time(), kind, message)
        except sqlite3.Error:
            pass  # closed mid-archive; the panel still shows the line

    def on_receiver_error(self, message: str) -> None:
        self.log(message)
        self.set_telemetry_status("error")
        self._receiver_error = message
        self.refresh_health()

    def refresh_health(self, uptime_ms: int | None = None) -> None:
        """Re-evaluate every health item; uptime is passed only with a new packet."""
        if not hasattr(self, "health_page"):
            return
        age = None if self.last_telemetry_time is None else time.monotonic() - self.last_telemetry_time
        link = link_status(self.client.connected, age, self._receiver_error)
        items = evaluate_health(self._health_packet, self._health_pid, link)
        events = self.health_events.update(items, datetime.now(), uptime_ms)
        for event in events:
            self._record_event("health", f"{event.state}: {event.text}")
        issues = active_issues(items)
        self._health_items = items
        self.health_page.show_health(items, issues, events)
        if any(issue.state == "error" for issue in issues):
            self.set_health_dot("error")
        elif issues:
            self.set_health_dot("warning")
        else:
            self.set_health_dot("ok" if link.downlink == "receiving" else "unknown")
        self.dashboard.show_status(issues, items, self._link_summary(age))

    def _link_summary(self, packet_age_s: float | None) -> str:
        """One line on the board and downlink for the Dashboard."""
        if packet_age_s is None or self._health_packet is None:
            return "No telemetry yet"
        parts = [
            f"Board up {format_uptime(self._health_packet.timestamp)}",
            f"last packet {packet_age_s:.1f} s ago",
        ]
        loss = self.packet_loss
        if loss.received:
            parts.append(f"{loss.lost} of {loss.received + loss.lost} packets lost ({loss.loss_percent:.1f}%)")
        logging = self.csv_logger is not None and self.csv_logger.active
        parts.append("CSV logging" if logging else "CSV off")
        return "  ·  ".join(parts)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._closing = True
        self.setEnabled(False)
        self.telemetry_receiver.stop()
        self.telemetry_receiver.wait(1000)
        self.command_dispatcher.shutdown()
        for dialog in list(self.plot_dialogs.values()):
            dialog.close()
        self.plot_dialogs.clear()
        self.plot_dialog_refs.clear()
        self.adc_db.close()
        if self.csv_logger is not None:
            self.csv_logger.stop()
        self.client.disconnect()
        super().closeEvent(event)
