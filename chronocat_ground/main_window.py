from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import time

from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QMainWindow,
    QPushButton,
    QWidget,
)

from .command_client import CommandClient
from .command_dispatcher import CommandDispatcher, CommandRequest
from .protocol import (
    COMMAND_GEIGER_CLEAR_HISTORY,
    COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
    COMMAND_GEIGER_RESET_STATS,
    COMMAND_HEATER_SET_KP,
    COMMAND_HEATER_SET_KD,
    COMMAND_HEATER_SET_KI,
    COMMAND_HEATER_SET_TARGET,
    COMMAND_HEATER_SET_MANUAL_DUTY,
    COMMAND_HEATER_RETURN_TO_PID,
    COMMAND_HEATER_ALL_ON,
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
    decode_heater_target_c,
    encode_heater_gain,
    encode_heater_target_c,
    geiger_reset_actions_name,
    geiger_error_names,
    heater_pid_averages,
    status_name,
    TELEMETRY_FLAG_SD_LOG_ACTIVE,
    TELEMETRY_FLAG_SD_LOG_ERROR,
    telemetry_health_name,
    telemetry_value_name,
    tcp_status_name,
)
from .heater_page import HeaterControlPage
from .heater_safety import all_heaters_safe
from .pid_page import PidPage
from .telemetry_csv import CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY, TelemetryCsvLogger
from .telemetry_db import TelemetryDb
from .telemetry_history import TelemetryHistory
from .telemetry_receiver import TelemetryReceiver
from .ui.widgets import SampleCard, StatCard, ValueTable
from .ui.styles import APPLICATION_STYLE
from .ui.main_window_pages import MainWindowPagesMixin


VIEW_MONITORING = "MONITORING"
VIEW_RADIATION = "RADIATION"
VIEW_SAMPLES = "SAMPLES"
VIEW_TEMPERATURE = "HEATING"
VIEW_HEATER_TEST = "HEATER TEST"
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
    bulk_duty: int | None = None


class MainWindow(MainWindowPagesMixin, QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("chronocat_ground")
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
        self.adc_db = TelemetryDb("chronocat_adc.db", async_writes=True)
        self.telemetry_history = TelemetryHistory(self.adc_db)
        self.sample_cards: list[SampleCard] = []
        self.plot_dialogs: dict[str, QDialog] = {}
        self.plot_dialog_refs: dict[str, dict[str, object]] = {}
        self.csv_logger: TelemetryCsvLogger | None = None
        self.pending_heater_command: PendingHeaterCommand | None = None
        self.heater_temperature_safe = False
        self.heater_page: HeaterControlPage | None = None
        self.pid_page: PidPage | None = None
        self.geiger_2_widgets: list[QWidget] = []

        self._settings = QSettings("chronocat", "chronocat_ground")
        self.geiger_test_mode = self._settings.value("geiger_test_mode", False, type=bool)

        self.telemetry_receiver = TelemetryReceiver(DEFAULT_TELEMETRY_PORT)
        self.telemetry_receiver.packet_received.connect(self.on_telemetry_packet)
        self.telemetry_receiver.receive_error.connect(self.on_receiver_error)

        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_telemetry_age)
        self.age_timer.start(1000)

        self.setCentralWidget(self.build_ui())
        self.apply_style()

        self.geiger_2_widgets = [
            self.geiger_2_dose_rate_card,
            self.geiger_2_total_dose_card,
            self.geiger_2_hv_card,
            self.geiger_2_errors_card,
            self.monitoring_geiger_2_title,
            self.monitoring_geiger_2_plot,
            self.radiation_2_dose_rate_card,
            self.radiation_2_total_dose_card,
            self.radiation_2_hv_card,
            self.radiation_2_errors_card,
            self.radiation_geiger_2_title,
            self.radiation_2_plot_status,
            self.radiation_geiger_2_plot,
            self.radiation_2_table_panel,
        ]

        if self.geiger_test_mode:
            self._apply_geiger_test_mode(True)

        self.switch_view(VIEW_MONITORING)
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

        self.csv_log_button.setText("Stop CSV Log")
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
        self.csv_log_button.setText("Start CSV Log")
        self.csv_log_status.setText(f"CSV logging stopped ({packet_count})")
        self.log(f"CSV logging stopped: {path} ({packet_count} packet(s))")

    def update_connection_state(self) -> None:
        connected = self.client.connected
        command_in_progress = self.command_dispatcher.busy
        self.connection_label.setText(
            "Connecting" if self.connection_pending else ("Connected" if connected else "Disconnected")
        )
        if self.connection_label.property("connected") != connected:
            self.connection_label.setProperty("connected", connected)
            self.connection_label.style().unpolish(self.connection_label)
            self.connection_label.style().polish(self.connection_label)
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(not command_in_progress)
        self.host_input.setEnabled(not command_in_progress)
        self.port_input.setEnabled(not command_in_progress)

        self.health_table.set_value("Command Connection", "Connected" if connected else "Disconnected")

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

        if self.heater_page is not None:
            self.heater_page.set_connected(connected)
            self.heater_page.set_command_busy(command_in_progress)
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

    def command_completed(self, _request: CommandRequest, response: CommandResponse) -> None:
        self.update_connection_state()

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
            bulk_duty = pending.bulk_duty
            self.pending_heater_command = None

            if response.status == 0 and response.arg1 == expected_arg1:
                if value_kind == "manual_row" and heater_id is not None:
                    if self.heater_page is not None:
                        self.heater_page.apply_manual_response(heater_id, response)
                elif value_kind == "bulk_on" or value_kind == "bulk_off":
                    if self.heater_page is not None:
                        self.heater_page.apply_bulk_response(bulk_duty, response)
                    if self.pid_page is not None:
                        self.pid_page.set_command_status(
                            "all heaters off confirmed"
                            if bulk_duty is None
                            else f"all heaters on at {response.arg2}/1000 confirmed"
                        )
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
                if value_kind == "manual_row" and heater_id is not None:
                    if self.heater_page is not None:
                        self.heater_page.apply_manual_response(heater_id, response)
                elif value_kind in ("bulk_on", "bulk_off"):
                    if self.heater_page is not None:
                        self.heater_page.apply_bulk_response(bulk_duty, response)
                    if self.pid_page is not None:
                        self.pid_page.set_command_status(
                            f"rejected: {status_name(response.status)}"
                        )
                elif self.pid_page is not None:
                    self.pid_page.set_command_status(f"rejected: {status_name(response.status)}")
                self.log(f"Heater {param_name} rejected: {status_name(response.status)}")
        else:
            self.pending_heater_command = None
            self.log_response(response)

    def command_failed(self, _request: CommandRequest, message: str) -> None:
        if self.pending_heater_command is not None:
            pending = self.pending_heater_command
            param_name = pending.param_name
            heater_id = pending.heater_id
            self.pending_heater_command = None
            if self.heater_page is not None and (
                heater_id is not None or param_name.startswith("all heaters")
            ):
                self.heater_page.set_command_error(heater_id, message)
            if self.pid_page is not None:
                self.pid_page.set_command_status(f"failed: {message}")
            self.log(f"Heater {param_name} command failed: {message}")
        else:
            self.log(f"Command failed: {message}")
        self.client.disconnect()
        self.update_connection_state()

    def send_geiger_command(self, command: int, action: str, timeout: float | None = None) -> None:
        self.log(f"Sending Geiger command: {action}")
        self.send_command(command, 0, 0, timeout=timeout)

    def set_manual_heater_duty(self, heater_id: int, duty_permille: int) -> None:
        if not self.client.connected or self.command_dispatcher.busy:
            return
        if self.heater_page is None:
            return
        self.pending_heater_command = PendingHeaterCommand(
            command=COMMAND_HEATER_SET_MANUAL_DUTY,
            param_name=f"H{heater_id} manual duty",
            encoded_value=duty_permille,
            value_kind="manual_row",
            expected_arg1=heater_id,
            heater_id=heater_id,
        )
        self.heater_page.set_row_pending(heater_id, duty_permille)
        self.log(f"Setting H{heater_id} manual duty to {duty_permille}/1000")
        self.send_command(COMMAND_HEATER_SET_MANUAL_DUTY, heater_id, duty_permille)

    def turn_all_heaters_on(self, duty_permille: int) -> None:
        if not self.client.connected or self.command_dispatcher.busy:
            return
        if self.heater_page is None:
            return
        if not self.heater_temperature_safe:
            self.heater_page.set_command_error(
                None, "12 valid temperature sensors below 65 C required"
            )
            return
        self.pending_heater_command = PendingHeaterCommand(
            command=COMMAND_HEATER_ALL_ON,
            param_name="all heaters on",
            encoded_value=duty_permille,
            value_kind="bulk_on",
            bulk_duty=duty_permille,
        )
        self.heater_page.set_bulk_pending(duty_permille)
        self.log(f"Turning all heaters on at {duty_permille}/1000")
        self.send_command(COMMAND_HEATER_ALL_ON, 0, duty_permille)

    def turn_all_heaters_off(self) -> None:
        if not self.client.connected or self.command_dispatcher.busy:
            return
        if self.heater_page is None:
            return
        self.pending_heater_command = PendingHeaterCommand(
            command=COMMAND_HEATER_ALL_OFF,
            param_name="all heaters off",
            encoded_value=0,
            value_kind="bulk_off",
        )
        self.heater_page.set_bulk_pending(None)
        if self.pid_page is not None:
            self.pid_page.set_command_status("sending all-off...")
        self.log("Turning all heaters off")
        self.send_command(COMMAND_HEATER_ALL_OFF, 0, 0)

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

    def update_geiger_detail_table(
        self, table: ValueTable, reading: GeigerReading | None
    ) -> None:
        if reading is None or not reading.valid:
            state = "unavailable" if reading is None else "invalid"
            table.set_value("Geiger Valid", state)
            for name in (
                "Geiger Event ID",
                "Geiger Dose CPS",
                "Geiger Dose Rate CPS",
                "Geiger Total Dose Sv",
                "Geiger Dose Time Sec",
                "Geiger Stats Time Sec",
                "Geiger HV Voltage",
                "Geiger Stat Error %",
                "Geiger Stat Cell Count",
                "Geiger Error Flags",
            ):
                table.set_value(name, "—")
            return

        table.set_value("Geiger Valid", str(reading.valid))
        table.set_value("Geiger Event ID", str(reading.event_id))
        table.set_value("Geiger Dose CPS", f"{reading.dose_cps:.17g}")
        table.set_value("Geiger Dose Rate CPS", f"{reading.dose_rate_cps:.9g}")
        table.set_value("Geiger Total Dose Sv", f"{reading.total_dose_sv:.9g}")
        table.set_value("Geiger Dose Time Sec", str(reading.dose_time_sec))
        table.set_value("Geiger Stats Time Sec", str(reading.stats_time_sec))
        table.set_value("Geiger HV Voltage", str(reading.hv_voltage))
        table.set_value("Geiger Stat Error %", str(reading.stat_error_percent))
        table.set_value("Geiger Stat Cell Count", str(reading.stat_cell_count))
        table.set_value(
            "Geiger Error Flags",
            f"0x{reading.error_flags:04x} ({self.geiger_error_text(reading)})",
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
        self.heater_temperature_safe = all_heaters_safe(packet)
        if self.heater_page is not None:
            self.heater_page.set_temperature_safety(self.heater_temperature_safe)
        self.set_telemetry_status("receiving")

        tcp_state = tcp_status_name(packet.tcp_status)
        health = telemetry_health_name(packet.health_code)
        flags = f"0x{packet.flags:04x}"
        temp_summary = self.format_temperature_summary(packet)
        adc_summary = self.format_adc_summary(packet)

        self.seq_card.set_value(str(packet.counter))
        self.tick_card.set_value(str(packet.timestamp))
        self.tcp_card.set_value(health)
        self.count_card.set_value(str(history.packet_count))
        geiger_1 = packet.geiger_reading(0)
        geiger_2 = packet.geiger_reading(1)
        self.update_geiger_cards(
            geiger_1,
            self.geiger_dose_rate_card,
            self.geiger_total_dose_card,
            self.geiger_hv_card,
            self.geiger_errors_card,
        )
        if not self.geiger_test_mode:
            self.update_geiger_cards(
                geiger_2,
                self.geiger_2_dose_rate_card,
                self.geiger_2_total_dose_card,
                self.geiger_2_hv_card,
                self.geiger_2_errors_card,
            )
        self.update_geiger_cards(
            geiger_1,
            self.radiation_dose_rate_card,
            self.radiation_total_dose_card,
            self.radiation_hv_card,
            self.radiation_errors_card,
        )
        if not self.geiger_test_mode:
            self.update_geiger_cards(
                geiger_2,
                self.radiation_2_dose_rate_card,
                self.radiation_2_total_dose_card,
                self.radiation_2_hv_card,
                self.radiation_2_errors_card,
            )
        self.timestamp_label.setText(
            f"RECEPTION TIMESTAMP: {datetime.fromtimestamp(received_wall).strftime('%H:%M:%S')}"
        )

        for counter_id, points in enumerate(history.geiger_points):
            reading = (geiger_1, geiger_2)[counter_id]
            if reading is None or not reading.valid:
                continue
            if counter_id == 0:
                self.monitoring_geiger_plot.set_points(points)
                self.radiation_geiger_plot.set_points(points)
                self.radiation_plot_status.setText(f"{len(points)}/300 points")
            elif not self.geiger_test_mode:
                self.monitoring_geiger_2_plot.set_points(points)
                self.radiation_geiger_2_plot.set_points(points)
                self.radiation_2_plot_status.setText(f"{len(points)}/300 points")

        materials = ["TIPs-pentacene", "diF-TES-ADT", "Rubrene"]
        device_types = ["Device 1a", "Device 2a", "Device 1b", "Device 2b"]
        for reading in packet.ad7177_readings:
            if reading.slot < len(self.sample_cards):
                points = history.adc_points[reading.slot]
                self.sample_cards[reading.slot].set_points(points)
                self.sample_cards[reading.slot].set_reading(
                    f"0x{reading.raw24:06x} ({reading.raw24})",
                    f"0x{reading.status:02x} ({ad7177_status_names(reading.status)})",
                )
            ch = reading.slot % 3
            dev_off = reading.slot // 3
            self.samples_summary_table.set_value(
                f"{materials[ch]} {device_types[dev_off]}",
                f"0x{reading.raw24:06x} ({reading.raw24})",
            )
        if history.adc_average_points:
            self.monitoring_adc_average_plot.set_points(history.adc_average_points)
        self.update_plot_dialogs()

        self.telemetry_table.set_value("AD7177 Readings", adc_summary)
        self.telemetry_table.set_value("Temperature Measurements", temp_summary)
        heater_duty = packet.heater_duty_permille
        if combined_packet is not None:
            _, average_duty = heater_pid_averages(combined_packet.pid.heaters)
            heater_duty = "—" if average_duty is None else f"{average_duty:.1f}"
        self.telemetry_table.set_value("Heater Duty (permille)", str(heater_duty))
        self.telemetry_table.set_value("Subsystem Health Indicators", health)
        for number, reading in ((1, geiger_1), (2, geiger_2)):
            if number == 2 and self.geiger_test_mode:
                continue
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
        self.telemetry_table.set_value("ADC Legacy Valid Mask", f"0x{packet.os_adc_valid_mask:04x}")
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
        self.packet_table.set_value("ADC Legacy Valid Mask", f"0x{packet.os_adc_valid_mask:04x}")
        self.packet_table.set_value("AD7177 Readings", adc_summary)
        self.update_packet_geiger_fields(1, geiger_1)
        if not self.geiger_test_mode:
            self.update_packet_geiger_fields(2, geiger_2)
        self.packet_table.set_value("TCP Server", tcp_state)

        self.update_geiger_detail_table(self.radiation_table, geiger_1)
        if not self.geiger_test_mode:
            self.update_geiger_detail_table(self.radiation_2_table, geiger_2)

        self.health_table.set_value("TCP Server", tcp_state)
        self.health_table.set_value("Flags", flags)
        self.health_table.set_value("Health Code", health)
        self.health_table.set_value("Packets Received", str(history.packet_count))
        self.health_table.set_value("Last Telemetry", "now")

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
            text = "SD: ERROR"
            state = "error"
            tooltip = "Firmware SD temperature logging has failed"
        elif (flags & TELEMETRY_FLAG_SD_LOG_ACTIVE) != 0:
            text = "SD: LOGGING"
            state = "active"
            tooltip = "Firmware SD temperature logging is active"
        else:
            text = "SD: OFF"
            state = "off"
            tooltip = "Firmware SD temperature logging is not active"
        self.sd_log_label.setText(text)
        self.sd_log_label.setToolTip(tooltip)
        if self.sd_log_label.property("sd_status") != state:
            self.sd_log_label.setProperty("sd_status", state)
            self.sd_log_label.style().unpolish(self.sd_log_label)
            self.sd_log_label.style().polish(self.sd_log_label)

    def set_telemetry_status(self, status: str) -> None:
        labels = {
            "waiting": ("Telemetry: WAITING", "No telemetry packet has been received"),
            "receiving": ("Telemetry: RECEIVING", "Telemetry is arriving"),
            "stale": ("Telemetry: STALE", "No telemetry packet has arrived recently"),
            "error": ("Telemetry: ERROR", "Telemetry receiver reported an error"),
        }
        text, tooltip = labels.get(status, labels["error"])
        self.telemetry_label.setText(text)
        self.telemetry_label.setToolTip(tooltip)
        if self.telemetry_label.property("telemetry_status") != status:
            self.telemetry_label.setProperty("telemetry_status", status)
            self.telemetry_label.style().unpolish(self.telemetry_label)
            self.telemetry_label.style().polish(self.telemetry_label)

        active = status == "receiving"
        self.telemetry_state.setText("Telemetry receiving" if active else text)
        if self.telemetry_state.property("active") != active:
            self.telemetry_state.setProperty("active", active)
            self.telemetry_state.style().unpolish(self.telemetry_state)
            self.telemetry_state.style().polish(self.telemetry_state)

    def format_temperature_summary(self, packet: TelemetryPacket) -> str:
        valid_count = sum(1 for index in range(len(packet.temperatures)) if packet.temperature_valid(index))
        return f"{valid_count}/{len(packet.temperatures)} valid"

    def format_adc_summary(self, packet: TelemetryPacket) -> str:
        active_count = sum(1 for reading in packet.ad7177_readings if reading.word != 0)
        first = packet.ad7177_reading(0)
        return (
            f"{active_count}/{len(packet.os_adc_readings)} nonzero; "
            f"ADC0 CH0 raw24=0x{first.raw24:06x} status=0x{first.status:02x}"
        )

    def update_telemetry_age(self) -> None:
        if self.client.connected and not self.client.check_connection():
            self.update_connection_state()
            self.log("Connection lost")

        if self.last_telemetry_time is None:
            self.set_telemetry_status("waiting")
            return

        age = time.monotonic() - self.last_telemetry_time
        age_text = f"{age:.1f}s ago"
        self.telemetry_table.set_value("Last Seen", age_text)
        self.health_table.set_value("Last Telemetry", age_text)

        active = age < 2.5
        self.set_telemetry_status("receiving" if active else "stale")
        if not active:
            self.heater_temperature_safe = False
            if self.heater_page is not None:
                self.heater_page.set_temperature_safety(False)

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")

    def on_receiver_error(self, message: str) -> None:
        self.log(message)
        self.set_telemetry_status("error")

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
