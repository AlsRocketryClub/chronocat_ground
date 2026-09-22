from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
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
    COMMAND_HEATER_SET_MANUAL_DUTY,
    COMMAND_HEATER_RETURN_TO_PID,
    COMMAND_HEATER_ALL_OFF,
    DEFAULT_TELEMETRY_PORT,
    CommandResponse,
    CombinedTelemetryPacket,
    PidTelemetryPacket,
    GeigerReading,
    TelemetryPacket,
    ad7177_status_names,
    command_name,
    decode_heater_gain,
    decode_float32_args,
    decode_heater_target_c,
    encode_heater_gain,
    encode_heater_target_c,
    geiger_reset_actions_name,
    geiger_error_names,
    heater_pid_averages,
    status_name,
    TELEMETRY_FLAG_ENABLED,
    TELEMETRY_FLAG_SD_LOG_ACTIVE,
    TELEMETRY_FLAG_SD_LOG_ERROR,
    telemetry_health_name,
    telemetry_value_name,
    tcp_status_name,
)
from .pid_page import PidPage
from .pid_profiles import PidProfile, profile_by_name
from .telemetry_csv import CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY, TelemetryCsvLogger
from .telemetry_db import DEFAULT_DATABASE_PATH, TelemetryDb
from .telemetry_history import TelemetryHistory, adc_point_for_mode
from .telemetry_receiver import TelemetryReceiver
from .ui.widgets import SampleCard, StatCard, ValueTable
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


def tri_state(healthy_count: int, total_count: int) -> str:
    """Red when nothing is healthy, yellow when some but not all is, green when all is."""
    if total_count <= 0:
        return "unknown"
    if healthy_count <= 0:
        return "error"
    if healthy_count >= total_count:
        return "healthy"
    return "warning"


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
        self.view_buttons: dict[str, QPushButton] = {}
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

        self.telemetry_receiver = TelemetryReceiver(DEFAULT_TELEMETRY_PORT)
        self.telemetry_receiver.packet_received.connect(self.on_telemetry_packet)
        self.telemetry_receiver.receive_error.connect(self.on_receiver_error)

        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_telemetry_age)
        self.age_timer.start(1000)

        self.apply_style()
        self.setCentralWidget(self.build_ui())

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

    def connection_failed(self, message: str) -> None:
        self.connection_pending = False
        self.log(f"Connect failed: {message}")
        self.log("Check that the board is flashed, linked, and reachable at this IP.")
        self.update_connection_state()

    def toggle_csv_logging(self) -> None:
        if self.csv_logger is not None and self.csv_logger.active:
            self.stop_csv_logging()
            return

        csv_mode = self.csv_mode_combo.currentData()
        self.csv_logger = TelemetryCsvLogger(mode=csv_mode, background_sync=True)
        try:
            self.csv_logger.start()
        except OSError as exc:
            self.csv_logger = None
            self.csv_log_status.setText("CSV logging failed")
            self.log(f"CSV logging failed: {exc}")
            return

        self.csv_log_button.setText("Stop CSV logging")
        self.csv_mode_combo.setEnabled(False)
        self.csv_log_status.setText(
            f"CSV ({self.csv_mode_combo.currentText()}): {self.csv_logger.path.name} (0)"
        )
        self.log(
            f"CSV logging started ({self.csv_mode_combo.currentText()}): "
            f"{self.csv_logger.path}"
        )

    def stop_csv_logging(self) -> None:
        if self.csv_logger is None:
            return
        path = self.csv_logger.path
        packet_count = self.csv_logger.packet_count
        self.csv_logger.stop()
        self.csv_logger = None
        self.csv_mode_combo.setEnabled(True)
        self.csv_log_button.setText("Start CSV logging")
        self.csv_log_status.setText(f"CSV logging stopped ({packet_count})")
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

        self.health_table.set_value("Uplink", "Connected" if connected else "Disconnected")
        self.health_table.set_state("Uplink", "healthy" if connected else "error")
        if self.last_telemetry_time is None:
            self.system_health_section.set_status(
                f"WAITING FOR DOWNLINK  ·  UPLINK {'CONNECTED' if connected else 'DISCONNECTED'}",
                "unknown" if connected else "warning",
            )

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
                self.geiger_xder_values[detector_id] = None
                self.set_geiger_xder_display(
                    detector_id, f"failed: {status_name(response.status)}"
                )
            else:
                try:
                    value = decode_float32_args(response.arg1, response.arg2)
                except ValueError as exc:
                    self.geiger_xder_values[detector_id] = None
                    self.set_geiger_xder_display(detector_id, f"invalid: {exc}")
                else:
                    self.geiger_xder_values[detector_id] = value
                    self.set_geiger_xder_display(detector_id, f"{value:.9g}")
                    self.log(f"Geiger {detector_id + 1} xDER: {value:.9g}")
            self.log_response(response)
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
                elif value_kind == "gain" and self.pid_page is not None:
                    decoded = decode_heater_gain(response.arg2)
                    if response.arg2 != encoded_value:
                        self.pid_page.set_command_status(
                            f"applied {decoded:.3f} "
                            f"(requested {decode_heater_gain(encoded_value):.3f})"
                        )
                    else:
                        self.pid_page.set_command_status(f"applied {decoded:.3f}")
                elif value_kind == "duty" and self.pid_page is not None:
                    self.pid_page.set_command_status(f"manual duty active: {response.arg2}/1000")
                elif self.pid_page is not None:
                    self.pid_page.set_command_status("PID mode active")
                self.log_response(response)
            else:
                if self.pid_page is not None:
                    self.pid_page.set_command_status(f"rejected: {status_name(response.status)}")
                self.log(f"Heater {param_name} rejected: {status_name(response.status)}")
        else:
            self.pending_heater_command = None
            self.log_response(response)

    def command_failed(self, request: CommandRequest, message: str) -> None:
        if request.command == COMMAND_GEIGER_READ_XDER:
            self.geiger_xder_values[request.arg1] = None
            self.set_geiger_xder_display(request.arg1, f"failed: {message}")
            self.log(f"Geiger {request.arg1 + 1} xDER command failed: {message}")
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
            (COMMAND_HEATER_SET_TARGET, "target", encode_heater_target_c(operation.target)),
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
        self.send_heater_parameter(
            COMMAND_HEATER_SET_TARGET,
            f"H{heater_id} target",
            encode_heater_target_c(value),
            "target",
            heater_id,
        )

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

    @staticmethod
    def geiger_error_text(reading: GeigerReading | None) -> str:
        if reading is None:
            return "unavailable"
        if not reading.valid:
            return "invalid"
        if not reading.error_flags:
            return "ok"
        return geiger_error_names(reading.error_flags)

    def update_geiger_cards(
        self,
        reading: GeigerReading | None,
        dose_rate_card: StatCard,
        total_dose_card: StatCard,
        hv_card: StatCard,
        errors_card: StatCard,
    ) -> None:
        if reading is None or not reading.valid:
            state = "unavailable" if reading is None else "invalid"
            dose_rate_card.set_value(state)
            total_dose_card.set_value(state)
            hv_card.set_value(state)
            errors_card.set_value(state)
            return

        dose_rate_card.set_value(f"{reading.dose_rate_cps:.9g}")
        total_dose_card.set_value(f"{reading.total_dose_sv:.9g}")
        hv_card.set_value(str(reading.hv_voltage))
        errors_card.set_value(self.geiger_error_text(reading))

    @staticmethod
    def update_geiger_rate_card(
        reading: GeigerReading | None, card: StatCard
    ) -> None:
        if reading is None or not reading.valid:
            card.set_value("unavailable" if reading is None else "invalid")
            return
        card.set_value(f"{reading.dose_rate_cps:.9g}")

    def update_geiger_detail_table(
        self, table: ValueTable, reading: GeigerReading | None, column: int
    ) -> None:
        if reading is None or not reading.valid:
            state = "unavailable" if reading is None else "invalid"
            table.set_value("Valid", state, column)
            for name in (
                "Event ID",
                "Dose (CPS)",
                "Dose rate (CPS)",
                "Total dose (Sv)",
                "Dose time (s)",
                "Statistics time (s)",
                "HV (V)",
                "Statistical error (%)",
                "Statistical cell count",
                "Error flags",
            ):
                table.set_value(name, "—", column)
            return

        table.set_value("Valid", str(reading.valid), column)
        table.set_value("Event ID", str(reading.event_id), column)
        table.set_value("Dose (CPS)", f"{reading.dose_cps:.17g}", column)
        table.set_value("Dose rate (CPS)", f"{reading.dose_rate_cps:.9g}", column)
        table.set_value("Total dose (Sv)", f"{reading.total_dose_sv:.9g}", column)
        table.set_value("Dose time (s)", str(reading.dose_time_sec), column)
        table.set_value("Statistics time (s)", str(reading.stats_time_sec), column)
        table.set_value("HV (V)", str(reading.hv_voltage), column)
        table.set_value("Statistical error (%)", str(reading.stat_error_percent), column)
        table.set_value("Statistical cell count", str(reading.stat_cell_count), column)
        table.set_value(
            "Error flags",
            f"0x{reading.error_flags:04x} ({self.geiger_error_text(reading)})",
            column,
        )

    def update_packet_geiger_fields(
        self, number: int, reading: GeigerReading | None
    ) -> None:
        prefix = f"Geiger {number}"
        if reading is None or not reading.valid:
            state = "unavailable" if reading is None else "invalid"
            self.packet_table.set_value(f"{prefix} Valid", state)
            for suffix in (
                "Error Flags",
                "Event ID",
                "Dose CPS",
                "Dose Rate CPS",
                "Total Dose Sv",
                "Dose Time Sec",
                "Stats Time Sec",
                "HV Voltage",
                "Stat Error %",
                "Stat Cell Count",
            ):
                self.packet_table.set_value(f"{prefix} {suffix}", "—")
            return

        self.packet_table.set_value(f"{prefix} Valid", str(reading.valid))
        self.packet_table.set_value(f"{prefix} Error Flags", f"0x{reading.error_flags:04x}")
        self.packet_table.set_value(f"{prefix} Event ID", str(reading.event_id))
        self.packet_table.set_value(f"{prefix} Dose CPS", f"{reading.dose_cps:.17g}")
        self.packet_table.set_value(f"{prefix} Dose Rate CPS", f"{reading.dose_rate_cps:.9g}")
        self.packet_table.set_value(f"{prefix} Total Dose Sv", f"{reading.total_dose_sv:.9g}")
        self.packet_table.set_value(f"{prefix} Dose Time Sec", str(reading.dose_time_sec))
        self.packet_table.set_value(f"{prefix} Stats Time Sec", str(reading.stats_time_sec))
        self.packet_table.set_value(f"{prefix} HV Voltage", str(reading.hv_voltage))
        self.packet_table.set_value(f"{prefix} Stat Error %", str(reading.stat_error_percent))
        self.packet_table.set_value(f"{prefix} Stat Cell Count", str(reading.stat_cell_count))

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
                self.pid_page.update_packet(packet.pid)
            packet = packet.standard
        elif isinstance(packet, PidTelemetryPacket):
            self.last_telemetry_time = received_monotonic
            self.update_sd_log_status(packet.flags)
            self.set_telemetry_status("receiving")
            if self.pid_page is not None:
                self.pid_page.update_packet(packet)
            if self.csv_logger is not None and self.csv_logger.active:
                try:
                    self.csv_logger.write_packet(
                        packet, source, datetime.fromtimestamp(received_wall)
                    )
                except OSError as exc:
                    self.log(f"PID CSV logging failed: {exc}")
                    self.stop_csv_logging()
                else:
                    self.csv_log_status.setText(
                        f"CSV ({self.csv_mode_combo.currentText()}): "
                        f"{self.csv_logger.path.name} ({self.csv_logger.packet_count})"
                    )
            return

        self.last_telemetry_time = received_monotonic
        history = self.telemetry_history.record(packet, received_monotonic, received_wall)
        if self.telemetry_history.database_error is not None:
            self.log(
                f"Telemetry database logging failed: "
                f"{self.telemetry_history.database_error}"
            )
            self.telemetry_history.database_error = None
        self.update_sd_log_status(packet.flags)
        self.set_telemetry_status("receiving")

        tcp_state = tcp_status_name(packet.tcp_status)
        health = telemetry_health_name(packet.health_code)
        flags = f"0x{packet.flags:04x}"
        temp_summary = self.format_temperature_summary(packet)
        adc_summary = self.format_adc_summary(packet)

        self.tcp_card.set_value(health)
        self.temperature_summary_card.set_value(temp_summary)
        valid_adc_count = sum(
            1 for reading in packet.ad7177_readings if packet.os_adc_valid(reading.slot)
        )
        self.adc_summary_card.set_value(
            f"{valid_adc_count}/{len(packet.ad7177_readings)} valid"
        )
        self.board_frame_index_card.set_value(f"{packet.counter:,}")
        self.session_frame_count_card.set_value(f"{history.packet_count:,}")
        geiger_1 = packet.geiger_reading(0)
        geiger_2 = packet.geiger_reading(1)
        self.update_geiger_rate_card(geiger_1, self.geiger_dose_rate_card)
        self.update_geiger_rate_card(geiger_2, self.geiger_2_dose_rate_card)
        self.update_geiger_cards(
            geiger_1,
            self.radiation_dose_rate_card,
            self.radiation_total_dose_card,
            self.radiation_hv_card,
            self.radiation_errors_card,
        )
        self.update_geiger_cards(
            geiger_2,
            self.radiation_2_dose_rate_card,
            self.radiation_2_total_dose_card,
            self.radiation_2_hv_card,
            self.radiation_2_errors_card,
        )
        self.timestamp_label.setText(
            f"Received {datetime.fromtimestamp(received_wall).strftime('%H:%M:%S')}"
        )

        geiger_series = (
            ("Geiger 1", history.geiger_points[0]),
            ("Geiger 2", history.geiger_points[1]),
        )
        self.monitoring_geiger_plot.set_series(geiger_series)
        self.radiation_geiger_plot.set_points(history.geiger_points[0])
        self.radiation_geiger_2_plot.set_points(history.geiger_points[1])
        self.radiation_plot_status.setText(f"{len(history.geiger_points[0])}/300 points")
        self.radiation_2_plot_status.setText(f"{len(history.geiger_points[1])}/300 points")

        self._last_adc_packet = packet
        self._last_adc_history = history
        self.refresh_sample_cards()
        if history.adc_average_points:
            self.monitoring_adc_average_plot.set_points(history.adc_average_points)
        self.update_plot_dialogs()

        self.telemetry_table.set_value("AD7177 Readings", adc_summary)
        self.telemetry_table.set_value("Temperature Measurements", temp_summary)
        heater_duty = packet.heater_duty_permille
        if combined_packet is not None:
            _, average_duty = heater_pid_averages(combined_packet.pid.heaters)
            heater_duty = "—" if average_duty is None else f"{average_duty:.1f}"
            enabled_count = sum(
                1 for reading in combined_packet.pid.heaters if reading.pid_enabled
            )
            self.heater_summary_card.set_value(
                f"{enabled_count}/{len(combined_packet.pid.heaters)} PID"
            )
        else:
            self.heater_summary_card.set_value("no PID data")
        self.telemetry_table.set_value("Heater Duty (permille)", str(heater_duty))
        self.telemetry_table.set_value("Subsystem Health Indicators", health)
        for number, reading in ((1, geiger_1), (2, geiger_2)):
            prefix = f"Geiger {number}"
            if reading is None or not reading.valid:
                state = "unavailable" if reading is None else "invalid"
                self.telemetry_table.set_value(f"{prefix} Valid", state)
                self.telemetry_table.set_value(f"{prefix} Dose Rate (CPS)", "—")
                self.telemetry_table.set_value(f"{prefix} Total Dose (Sv)", "—")
                self.telemetry_table.set_value(f"{prefix} HV Voltage", "—")
                self.telemetry_table.set_value(f"{prefix} Error Flags", "—")
            else:
                self.telemetry_table.set_value(f"{prefix} Valid", str(reading.valid))
                self.telemetry_table.set_value(
                    f"{prefix} Dose Rate (CPS)", f"{reading.dose_rate_cps:.9g}"
                )
                self.telemetry_table.set_value(
                    f"{prefix} Total Dose (Sv)", f"{reading.total_dose_sv:.9g}"
                )
                self.telemetry_table.set_value(f"{prefix} HV Voltage", str(reading.hv_voltage))
                self.telemetry_table.set_value(
                    f"{prefix} Error Flags",
                    f"0x{reading.error_flags:04x} ({self.geiger_error_text(reading)})",
                )
        self.telemetry_table.set_value("Packet Timestamp (ms)", str(packet.timestamp))
        self.telemetry_table.set_value("Health Code", health)
        self.telemetry_table.set_value("Counter", str(packet.counter))
        self.telemetry_table.set_value("Flags", flags)
        self.telemetry_table.set_value("Temperature Valid Mask", f"0x{packet.temperature_valid_mask:04x}")
        self.telemetry_table.set_value("ADC Valid Mask", f"0x{packet.os_adc_valid_mask:04x}")
        self.telemetry_table.set_value("Source", source)
        self.telemetry_table.set_value("Last Seen", "now")

        self.packet_table.set_value("Version", str(packet.version))
        self.packet_table.set_value("Message Type", str(packet.message_type))
        self.packet_table.set_value("Flags", flags)
        self.packet_table.set_value("Payload Length", str(packet.payload_length))
        self.packet_table.set_value("Packet Timestamp (ms)", str(packet.timestamp))
        self.packet_table.set_value("Counter", str(packet.counter))
        self.packet_table.set_value("Health Code", health)
        self.packet_table.set_value("Temperature Valid Mask", f"0x{packet.temperature_valid_mask:04x}")
        self.packet_table.set_value("Temperature Sensors", temp_summary)
        self.packet_table.set_value("ADC Valid Mask", f"0x{packet.os_adc_valid_mask:04x}")
        self.packet_table.set_value("AD7177 Readings", adc_summary)
        self.update_packet_geiger_fields(1, geiger_1)
        self.update_packet_geiger_fields(2, geiger_2)
        self.packet_table.set_value("TCP Server", tcp_state)

        self.update_geiger_detail_table(self.radiation_table, geiger_1, 1)
        self.update_geiger_detail_table(self.radiation_table, geiger_2, 2)
        self.update_health_tables(packet, combined_packet, tcp_state, health)

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
                self.csv_log_status.setText(
                    f"CSV ({self.csv_mode_combo.currentText()}): "
                    f"{self.csv_logger.path.name} ({self.csv_logger.packet_count})"
                )

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

    def update_health_tables(
        self,
        packet: TelemetryPacket,
        combined_packet: CombinedTelemetryPacket | None,
        tcp_state: str,
        health: str,
    ) -> None:
        """Project the latest downlink into compact, color-coded health tables."""
        telemetry_enabled = bool(packet.flags & TELEMETRY_FLAG_ENABLED)
        tcp_healthy = packet.tcp_status == 4
        firmware_healthy = packet.health_code == 0
        sd_error = bool(packet.flags & TELEMETRY_FLAG_SD_LOG_ERROR)
        sd_active = bool(packet.flags & TELEMETRY_FLAG_SD_LOG_ACTIVE)

        self.health_table.set_value("Downlink", "receiving")
        self.health_table.set_state("Downlink", "healthy")
        self.health_table.set_value("Last Telemetry", "now")
        self.health_table.set_state("Last Telemetry", "healthy")
        self.health_table.set_value("Firmware Health", health)
        self.health_table.set_state("Firmware Health", "healthy" if firmware_healthy else "error")
        self.health_table.set_value("TCP Server", tcp_state)
        self.health_table.set_state("TCP Server", "healthy" if tcp_healthy else "error")
        if sd_error:
            sd_text, sd_state = "error", "error"
        elif sd_active:
            sd_text, sd_state = "logging", "healthy"
        else:
            sd_text, sd_state = "off", "warning"
        self.health_table.set_value("SD Temperature Logger", sd_text)
        self.health_table.set_state("SD Temperature Logger", sd_state)
        system_state = (
            "error"
            if not firmware_healthy or not tcp_healthy or sd_error
            else "healthy" if self.client.connected else "warning"
        )
        self.system_health_section.set_status(
            f"FIRMWARE {health.upper()}  ·  DOWNLINK LIVE  ·  SD {sd_text.upper()}",
            system_state,
        )

        valid_temperature_count = 0
        for index, raw_value in enumerate(packet.temperatures):
            valid = packet.temperature_valid(index)
            temperature = packet.temperature_c(index)
            if valid:
                valid_temperature_count += 1
                value = f"{temperature:.2f} C"
                state = "error" if temperature is not None and temperature >= 65.0 else "healthy"
            else:
                value = f"invalid (raw {raw_value})"
                state = "warning"
            name = f"TMP117-{index + 1}"
            self.health_temperature_table.set_value(name, value, 1)
            self.health_temperature_table.set_value(name, "OK" if state == "healthy" else state, 2)
            self.health_temperature_table.set_state(name, state, 2)
        self.health_table.set_value(
            "Temperature Sensors", f"{valid_temperature_count}/{len(packet.temperatures)} valid"
        )
        self.health_table.set_state(
            "Temperature Sensors",
            "healthy" if valid_temperature_count == len(packet.temperatures) else "warning",
        )
        self.temperature_health_section.set_status(
            f"{valid_temperature_count} OF {len(packet.temperatures)} SENSORS VALID",
            "healthy" if valid_temperature_count == len(packet.temperatures) else "warning",
        )

        valid_adc_count = 0
        for reading in packet.ad7177_readings:
            valid = packet.os_adc_valid(reading.slot)
            has_error = reading.has_error
            if valid and not has_error:
                valid_adc_count += 1
                state = "healthy"
                value = f"{reading.voltage:.6f} V"
            elif has_error:
                state = "error"
                value = f"{reading.voltage:.6f} V ({ad7177_status_names(reading.status)})"
            else:
                state = "warning"
                value = f"stale {reading.voltage:.6f} V"
            name = f"ADC{reading.adc_index} CH{reading.channel_index}"
            self.health_adc_table.set_value(name, value, 1)
            self.health_adc_table.set_value(name, "OK" if state == "healthy" else state, 2)
            self.health_adc_table.set_state(name, state, 2)
        adc_state = tri_state(valid_adc_count, len(packet.ad7177_readings))
        self.health_table.set_value("AD7177 Channels", f"{valid_adc_count}/{len(packet.ad7177_readings)} healthy")
        self.health_table.set_state("AD7177 Channels", adc_state)
        self.adc_health_section.set_status(
            f"{valid_adc_count} OF {len(packet.ad7177_readings)} CHANNELS HEALTHY",
            adc_state,
        )

        healthy_geigers = 0
        for counter_id in range(2):
            reading = packet.geiger_reading(counter_id)
            row = f"Geiger {counter_id + 1}"
            if reading is None or not reading.valid:
                dose_rate, hv, state = "—", "—", "warning"
            else:
                dose_rate = f"{reading.dose_rate_cps:.9g} CPS"
                hv = f"{reading.hv_voltage} V"
                state = "error" if reading.error_flags else "healthy"
                if state == "healthy":
                    healthy_geigers += 1
            self.health_geiger_table.set_value(row, dose_rate, 1)
            self.health_geiger_table.set_value(row, hv, 2)
            self.health_geiger_table.set_value(row, "OK" if state == "healthy" else state, 3)
            self.health_geiger_table.set_state(row, state, 3)
        geiger_state = tri_state(healthy_geigers, 2)
        self.health_table.set_value("Geiger Detectors", f"{healthy_geigers}/2 healthy")
        self.health_table.set_state("Geiger Detectors", geiger_state)
        self.radiation_health_section.set_status(
            f"{healthy_geigers} OF 2 DETECTORS HEALTHY",
            geiger_state,
        )

        heater_count = 0
        healthy_heaters = 0
        if combined_packet is not None:
            for reading in combined_packet.pid.heaters:
                if not 0 <= reading.heater_id < 12:
                    continue
                heater_count += 1
                if reading.result >= 7:
                    state = "error"
                elif not reading.sensor_mapped or not reading.sensor_valid or reading.result >= 5:
                    state = "warning"
                else:
                    state = "healthy"
                    healthy_heaters += 1
                row = f"H{reading.heater_id}"
                value = "—" if reading.temperature_c is None else f"{reading.temperature_c:.2f} C / {reading.duty_permille}‰"
                self.health_heater_table.set_value(row, value, 2)
                self.health_heater_table.set_value(row, "OK" if state == "healthy" else state, 3)
                self.health_heater_table.set_state(row, state, 3)
        heater_text = "not present" if combined_packet is None else f"{healthy_heaters}/{heater_count} healthy"
        heater_state = "unknown" if combined_packet is None else tri_state(healthy_heaters, heater_count)
        self.health_table.set_value("Heater / PID Records", heater_text)
        self.health_table.set_state("Heater / PID Records", heater_state)
        self.heater_health_section.set_status(
            heater_text.upper(),
            heater_state,
        )
        self.health_table.setToolTip(
            f"Telemetry {'enabled' if telemetry_enabled else 'disabled'}; packet {packet.counter}"
        )

    def set_telemetry_status(self, status: str) -> None:
        labels = {
            "waiting": ("WAITING", "No telemetry received"),
            "receiving": ("RECEIVING", "Telemetry is arriving"),
            "stale": ("STALE", "No telemetry packet has arrived recently"),
            "error": ("ERROR", "Downlink reported an error"),
        }
        text, tooltip = labels.get(status, labels["error"])
        self.telemetry_indicator.set_status(text, status, tooltip)

    def format_temperature_summary(self, packet: TelemetryPacket) -> str:
        valid_count = sum(1 for index in range(len(packet.temperatures)) if packet.temperature_valid(index))
        return f"{valid_count}/{len(packet.temperatures)} valid"

    def format_adc_summary(self, packet: TelemetryPacket) -> str:
        active_count = sum(
            1 for reading in packet.ad7177_readings if packet.os_adc_valid(reading.slot)
        )
        first = packet.ad7177_reading(0)
        return (
            f"{active_count}/{len(packet.os_adc_readings)} valid; "
            f"ADC0 CH0 {first.voltage:.6f} V (raw24=0x{first.raw24:06x}) status=0x{first.status:02x}"
        )

    def toggle_samples_display_mode(self) -> None:
        self.samples_display_mode = (
            "raw" if self.samples_display_mode == "voltage" else "voltage"
        )
        self.samples_display_toggle.setText(
            "Showing: Raw24" if self.samples_display_mode == "raw" else "Showing: Voltage"
        )
        self.refresh_sample_cards()

    def refresh_sample_cards(self) -> None:
        packet = self._last_adc_packet
        history = self._last_adc_history
        if packet is None or history is None:
            return
        mode = self.samples_display_mode
        axis_label, axis_hover = ("Raw24", "Raw24") if mode == "raw" else ("Volts (V)", "Volts")
        materials = ["TIPs-pentacene", "diF-TES-ADT", "Rubrene"]
        device_types = ["Device 1a", "Device 2a", "Device 1b", "Device 2b"]
        for reading in packet.ad7177_readings:
            value_text = (
                f"0x{reading.raw24:06x}" if mode == "raw" else f"{reading.voltage:.6f} V"
            )
            if reading.slot < len(self.sample_cards):
                points = [
                    adc_point_for_mode(entry, mode)
                    for entry in history.adc_points[reading.slot]
                ]
                self.sample_cards[reading.slot].set_value_axis(axis_label, axis_hover)
                self.sample_cards[reading.slot].set_points(points)
                self.sample_cards[reading.slot].set_reading(
                    value_text,
                    f"0x{reading.status:02x} ({ad7177_status_names(reading.status)})",
                    packet.os_adc_valid(reading.slot),
                )
            ch = reading.slot % 3
            dev_off = reading.slot // 3
            self.samples_summary_table.set_value(
                f"{materials[ch]} {device_types[dev_off]}",
                value_text if packet.os_adc_valid(reading.slot) else f"INVALID/STALE (last {value_text})",
            )

    def _mark_downlink_derived_status_unknown(self) -> None:
        """Data that only arrives over a stale downlink can't be trusted; say so."""
        self.sd_log_indicator.set_status(
            "UNKNOWN", "unknown", "Downlink is stale; SD logging state can't be confirmed"
        )
        self.health_table.set_value("SD Temperature Logger", "unknown")
        self.health_table.set_state("SD Temperature Logger", "unknown")
        self.health_table.set_value("TCP Server", "unknown")
        self.health_table.set_state("TCP Server", "unknown")

    def update_telemetry_age(self) -> None:
        if self.client.connected and not self.client.check_connection():
            self.update_connection_state()
            self.log("Connection lost")

        if self.last_telemetry_time is None:
            self.set_telemetry_status("waiting")
            self.health_table.set_value("Downlink", f"waiting on UDP {DEFAULT_TELEMETRY_PORT}")
            self.health_table.set_state("Downlink", "unknown")
            self.system_health_section.set_status("WAITING FOR DOWNLINK", "unknown")
            self._mark_downlink_derived_status_unknown()
            return

        age = time.monotonic() - self.last_telemetry_time
        age_text = f"{age:.1f}s ago"
        self.telemetry_table.set_value("Last Seen", age_text)
        self.health_table.set_value("Last Telemetry", age_text)
        self.health_table.set_state("Last Telemetry", "healthy" if age < 2.5 else "warning")
        self.health_table.set_value("Downlink", "receiving" if age < 2.5 else "stale")
        self.health_table.set_state("Downlink", "healthy" if age < 2.5 else "warning")
        if age >= 2.5:
            self.system_health_section.set_status(
                f"DOWNLINK STALE  ·  LAST PACKET {age_text.upper()}", "warning"
            )
            self._mark_downlink_derived_status_unknown()

        active = age < 2.5
        self.set_telemetry_status("receiving" if active else "stale")
    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")

    def on_receiver_error(self, message: str) -> None:
        self.log(message)
        self.set_telemetry_status("error")
        self.health_table.set_value("Downlink", message)
        self.health_table.set_state("Downlink", "error")
        self.system_health_section.set_status("DOWNLINK ERROR", "error")

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
