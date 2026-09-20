from __future__ import annotations

import os
import sqlite3

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..pid_page import PidPage
from ..plot_widget import PlotWidget
from ..protocol import (
    AD7177_BIPOLAR_MIDSCALE,
    AD7177_CHANNEL_COUNT,
    AD7177_VREF_VOLTS,
    COMMAND_GEIGER_CLEAR_HISTORY,
    COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
    COMMAND_GEIGER_RESET_STATS,
    COMMAND_PING,
    COMMAND_TELEMETRY_SET,
    COMMAND_TELEMETRY_STATUS,
    DEFAULT_COMMAND_PORT,
    DEFAULT_DEVICE_HOST,
    DEFAULT_TELEMETRY_PORT,
    VALUE_OFF,
    VALUE_ON,
)
from ..telemetry_db import TelemetryDb, archive_database
from ..telemetry_history import adc_point_for_mode
from ..telemetry_csv import CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY
from .widgets import HealthSummaryCard, Panel, SampleCard, StatCard, ValueTable
from .status_widgets import StatusIndicator
from .pid_widgets import SENSOR_NAMES


def raw24_to_volts(raw24: int) -> float:
    return (raw24 - AD7177_BIPOLAR_MIDSCALE) / AD7177_BIPOLAR_MIDSCALE * AD7177_VREF_VOLTS


VIEW_DASHBOARD = "DASHBOARD"
VIEW_RADIATION = "RADIATION"
VIEW_SAMPLES = "SAMPLES"
VIEW_TEMPERATURE = "HEATING"
VIEW_HEALTH = "HEALTH"
VIEW_DIAGNOSTICS = "DIAGNOSTICS"
VIEW_SETTINGS = "SETTINGS"


class MainWindowPagesMixin:
    def build_ui(self) -> QWidget:
        root = QWidget()
        root.setObjectName("appShell")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(12)

        layout.addWidget(self.build_topbar())

        body = QHBoxLayout()
        body.setSpacing(12)
        layout.addLayout(body, 1)

        body.addWidget(self.build_sidebar())

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.pages.addWidget(self.scroll_page(self.build_dashboard_page()))
        self.pages.addWidget(self.scroll_page(self.build_radiation_page()))
        self.pages.addWidget(self.scroll_page(self.build_samples_page()))
        self.pages.addWidget(self.scroll_page(self.build_temperature_page()))
        self.pages.addWidget(self.scroll_page(self.build_health_page()))
        self.pages.addWidget(self.scroll_page(self.build_diagnostics_page()))
        self.pages.addWidget(self.scroll_page(self.build_settings_page()))
        body.addWidget(self.pages, 1)

        return root

    def build_topbar(self) -> QFrame:
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar_layout = QVBoxLayout(topbar)
        topbar_layout.setContentsMargins(12, 12, 12, 12)
        topbar_layout.setSpacing(8)

        title_row = QHBoxLayout()
        title_row.setSpacing(16)

        script_dir = os.path.dirname(os.path.dirname(__file__))
        logo_path = os.path.join(script_dir, "CHRONO-CAT_logo.png")
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            logo_pixmap = logo_pixmap.scaledToHeight(40, Qt.SmoothTransformation)
            logo_label = QLabel()
            logo_label.setPixmap(logo_pixmap)
            logo_label.setFixedSize(logo_pixmap.size())
            title_row.addWidget(logo_label)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("BEXUS 39 / CHRONO-CAT")
        eyebrow.setObjectName("eyebrow")
        header = QLabel("GROUND STATION")
        header.setObjectName("title")
        title_box.addWidget(eyebrow)
        title_box.addWidget(header)
        title_row.addLayout(title_box, 1)

        self.connection_indicator = StatusIndicator("UPLINK", "DISCONNECTED")
        self.connection_indicator.set_status("DISCONNECTED", "disconnected")
        title_row.addWidget(self.connection_indicator)
        self.telemetry_indicator = StatusIndicator("DOWNLINK", "WAITING")
        self.telemetry_indicator.set_status("WAITING", "waiting", "No telemetry received")
        title_row.addWidget(self.telemetry_indicator)
        self.sd_log_indicator = StatusIndicator("SD CARD", "UNKNOWN")
        self.sd_log_indicator.set_status("UNKNOWN", "unknown", "No SD logger status")
        title_row.addWidget(self.sd_log_indicator)

        topbar_layout.addLayout(title_row)

        control_row = QHBoxLayout()
        control_row.setSpacing(8)

        control_row.addWidget(QLabel("Host"))

        self.host_input = QLineEdit(DEFAULT_DEVICE_HOST)
        self.host_input.setMinimumWidth(120)
        control_row.addWidget(self.host_input)

        control_row.addWidget(QLabel("Port"))

        self.port_input = QLineEdit(str(DEFAULT_COMMAND_PORT))
        self.port_input.setMaximumWidth(80)
        control_row.addWidget(self.port_input)

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        control_row.addWidget(self.connect_button)

        self.csv_mode_combo = QComboBox()
        self.csv_mode_combo.addItem("Full telemetry", CSV_MODE_FULL)
        self.csv_mode_combo.addItem("Geiger only", CSV_MODE_GEIGER_ONLY)
        control_row.addWidget(self.csv_mode_combo)

        self.csv_log_button = QPushButton("Start CSV logging")
        self.csv_log_button.clicked.connect(self.toggle_csv_logging)
        control_row.addWidget(self.csv_log_button)

        self.csv_log_status = QLabel("Off")
        self.csv_log_status.setObjectName("smallNote")
        control_row.addWidget(self.csv_log_status)

        control_row.addStretch(1)

        topbar_layout.addLayout(control_row)

        return topbar

    def build_sidebar(self) -> Panel:
        sidebar = Panel()
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(150)
        sidebar.setMaximumWidth(210)

        for view in (
            VIEW_DASHBOARD,
            VIEW_RADIATION,
            VIEW_SAMPLES,
            VIEW_TEMPERATURE,
            VIEW_HEALTH,
            VIEW_DIAGNOSTICS,
            VIEW_SETTINGS,
        ):
            button = QPushButton(view)
            button.setObjectName("navButton")
            button.clicked.connect(lambda _checked=False, selected=view: self.switch_view(selected))
            self.view_buttons[view] = button
            sidebar.layout.addWidget(button)

        sidebar.layout.addStretch(1)
        return sidebar

    def scroll_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def build_dashboard_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.tcp_card = StatCard("SYSTEM HEALTH")
        self.temperature_summary_card = StatCard("TEMPERATURE SENSORS")
        self.adc_summary_card = StatCard("SAMPLE CHANNELS")
        self.geiger_dose_rate_card = StatCard("GEIGER 1 DOSE RATE (CPS)")
        self.geiger_2_dose_rate_card = StatCard("GEIGER 2 DOSE RATE (CPS)")
        self.heater_summary_card = StatCard("HEATER / PID")
        self.board_frame_index_card = StatCard("BOARD FRAME INDEX")
        self.session_frame_count_card = StatCard("GS FRAMES RECEIVED", value="0")

        cards = QGridLayout()
        cards.setSpacing(8)
        cards.addWidget(self.tcp_card, 0, 0)
        cards.addWidget(self.temperature_summary_card, 0, 1)
        cards.addWidget(self.adc_summary_card, 0, 2)
        cards.addWidget(self.board_frame_index_card, 0, 3)
        cards.addWidget(self.geiger_dose_rate_card, 1, 0)
        cards.addWidget(self.geiger_2_dose_rate_card, 1, 1)
        cards.addWidget(self.heater_summary_card, 1, 2)
        cards.addWidget(self.session_frame_count_card, 1, 3)
        layout.addLayout(cards)

        layout.addWidget(self.build_chart_panel())
        layout.addStretch(1)
        return page

    def build_chart_panel(self) -> Panel:
        chart_panel = Panel()
        chart_header = QHBoxLayout()
        chart_title = QLabel("GEIGER DOSE RATE HISTORY")
        chart_title.setObjectName("panelTitle")
        self.timestamp_label = QLabel("Received —")
        self.timestamp_label.setObjectName("smallNote")
        chart_header.addWidget(chart_title)
        chart_header.addStretch(1)
        chart_header.addWidget(self.timestamp_label)
        chart_panel.layout.addLayout(chart_header)
        self.monitoring_geiger_plot = PlotWidget(
            "Dose rate (CPS)", "No data", hover_label="CPS", interactive=False
        )
        self.monitoring_geiger_plot.on_double_click = lambda: self.show_geiger_dialog(0)
        chart_panel.layout.addWidget(self.monitoring_geiger_plot)
        average_title = QLabel("ADC CHANNEL AVERAGE")
        average_title.setObjectName("panelTitle")
        chart_panel.layout.addWidget(average_title)
        self.monitoring_adc_average_plot = PlotWidget(
            "Volts (V)", "No data", hover_label="Volts", interactive=False
        )
        chart_panel.layout.addWidget(self.monitoring_adc_average_plot)
        return chart_panel

    def build_radiation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        cards = QGridLayout()
        cards.setSpacing(8)
        self.radiation_dose_rate_card = StatCard("GEIGER 1 DOSE RATE (CPS)")
        self.radiation_total_dose_card = StatCard("GEIGER 1 TOTAL DOSE (Sv)")
        self.radiation_hv_card = StatCard("GEIGER 1 HV (V)")
        self.radiation_errors_card = StatCard("GEIGER 1 ERRORS")
        self.radiation_2_dose_rate_card = StatCard("GEIGER 2 DOSE RATE (CPS)")
        self.radiation_2_total_dose_card = StatCard("GEIGER 2 TOTAL DOSE (Sv)")
        self.radiation_2_hv_card = StatCard("GEIGER 2 HV (V)")
        self.radiation_2_errors_card = StatCard("GEIGER 2 ERRORS")
        cards.addWidget(self.radiation_dose_rate_card, 0, 0)
        cards.addWidget(self.radiation_total_dose_card, 0, 1)
        cards.addWidget(self.radiation_hv_card, 0, 2)
        cards.addWidget(self.radiation_errors_card, 0, 3)
        cards.addWidget(self.radiation_2_dose_rate_card, 1, 0)
        cards.addWidget(self.radiation_2_total_dose_card, 1, 1)
        cards.addWidget(self.radiation_2_hv_card, 1, 2)
        cards.addWidget(self.radiation_2_errors_card, 1, 3)
        layout.addLayout(cards)

        layout.addWidget(self.build_geiger_controls_panel())

        plots = QVBoxLayout()
        plots.setSpacing(12)

        geiger_1_panel = Panel("GEIGER 1 DOSE RATE")
        geiger_1_header = QHBoxLayout()
        self.radiation_plot_status = QLabel("0/300 points")
        self.radiation_plot_status.setObjectName("smallNote")
        geiger_1_header.addStretch(1)
        geiger_1_header.addWidget(self.radiation_plot_status)
        geiger_1_panel.layout.addLayout(geiger_1_header)
        self.radiation_geiger_plot = PlotWidget(
            "Dose rate (CPS)", "No data", hover_label="CPS", interactive=False
        )
        self.radiation_geiger_plot.on_double_click = lambda: self.show_geiger_dialog(0)
        self.radiation_geiger_plot.setMinimumHeight(280)
        geiger_1_panel.layout.addWidget(self.radiation_geiger_plot)
        plots.addWidget(geiger_1_panel)

        geiger_2_panel = Panel("GEIGER 2 DOSE RATE")
        geiger_2_header = QHBoxLayout()
        self.radiation_2_plot_status = QLabel("0/300 points")
        self.radiation_2_plot_status.setObjectName("smallNote")
        geiger_2_header.addStretch(1)
        geiger_2_header.addWidget(self.radiation_2_plot_status)
        geiger_2_panel.layout.addLayout(geiger_2_header)
        self.radiation_geiger_2_plot = PlotWidget(
            "Dose rate (CPS)", "No data", hover_label="CPS", interactive=False
        )
        self.radiation_geiger_2_plot.on_double_click = lambda: self.show_geiger_dialog(1)
        self.radiation_geiger_2_plot.setMinimumHeight(280)
        geiger_2_panel.layout.addWidget(self.radiation_geiger_2_plot)
        plots.addWidget(geiger_2_panel)
        layout.addLayout(plots)

        geiger_detail_rows = [
            ("Valid", "—"),
            ("Event ID", "—"),
            ("Dose (CPS)", "—"),
            ("Dose rate (CPS)", "—"),
            ("Total dose (Sv)", "—"),
            ("Dose time (s)", "—"),
            ("Statistics time (s)", "—"),
            ("HV (V)", "—"),
            ("Statistical error (%)", "—"),
            ("Statistical cell count", "—"),
            ("Error flags", "—"),
        ]
        self.radiation_table = ValueTable(
            [(name, "—", "—") for name, _value in geiger_detail_rows],
            ("Metric", "Geiger 1", "Geiger 2"),
        )
        self.radiation_table.expand_to_contents()
        table_panel = Panel("GEIGER PACKET DETAILS")
        table_panel.layout.addWidget(self.radiation_table)
        layout.addWidget(table_panel)
        layout.addStretch(1)
        return page

    def build_geiger_controls_panel(self) -> Panel:
        controls_panel = Panel("GEIGER DETECTOR CONTROLS")
        note = QLabel(
            "These commands write detector flash and can take a few seconds before telemetry updates."
        )
        note.setObjectName("smallNote")
        note.setWordWrap(True)
        controls_panel.layout.addWidget(note)

        self.geiger_reset_dose_button = QPushButton("Reset dose")
        self.geiger_clear_history_button = QPushButton("Clear history")
        self.geiger_reset_stats_button = QPushButton("Reset statistics")

        self.geiger_reset_dose_button.clicked.connect(
            lambda: self.send_geiger_command(
                COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
                "reset accumulated dose",
                timeout=7.0,
            )
        )
        self.geiger_clear_history_button.clicked.connect(
            lambda: self.send_geiger_command(COMMAND_GEIGER_CLEAR_HISTORY, "clear history", timeout=7.0)
        )
        self.geiger_reset_stats_button.clicked.connect(
            lambda: self.send_geiger_command(COMMAND_GEIGER_RESET_STATS, "reset statistics", timeout=7.0)
        )

        button_grid = QGridLayout()
        button_grid.setSpacing(8)
        button_grid.addWidget(self.geiger_reset_dose_button, 0, 0)
        button_grid.addWidget(self.geiger_clear_history_button, 0, 1)
        button_grid.addWidget(self.geiger_reset_stats_button, 0, 2)
        controls_panel.layout.addLayout(button_grid)

        xder_grid = QGridLayout()
        xder_grid.setSpacing(8)
        xder_grid.addWidget(QLabel("Geiger 1 xDER"), 0, 0)
        xder_grid.addWidget(QLabel("Geiger 2 xDER"), 1, 0)
        self.geiger_xder_labels = {
            0: QLabel("—"),
            1: QLabel("—"),
        }
        for label in self.geiger_xder_labels.values():
            label.setObjectName("smallNote")
        xder_grid.addWidget(self.geiger_xder_labels[0], 0, 1)
        xder_grid.addWidget(self.geiger_xder_labels[1], 1, 1)
        read_geiger_1_xder_button = QPushButton("Read Geiger 1 xDER")
        read_geiger_2_xder_button = QPushButton("Read Geiger 2 xDER")
        read_geiger_1_xder_button.clicked.connect(lambda: self.read_geiger_xder(0))
        read_geiger_2_xder_button.clicked.connect(lambda: self.read_geiger_xder(1))
        xder_grid.addWidget(read_geiger_1_xder_button, 0, 2)
        xder_grid.addWidget(read_geiger_2_xder_button, 1, 2)
        controls_panel.layout.addLayout(xder_grid)
        return controls_panel

    def build_samples_summary_panel(self) -> Panel:
        samples_panel = Panel()
        header = QHBoxLayout()
        title = QLabel("SAMPLES")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch(1)
        samples_panel.layout.addLayout(header)

        materials = ["TIPs-pentacene", "diF-TES-ADT", "Rubrene"]
        device_types = ["Device 1a", "Device 2a", "Device 1b", "Device 2b"]
        rows = []
        for slot in range(12):
            ch = slot % 3
            dev_off = slot // 3
            mat_name = materials[ch]
            dev_name = device_types[dev_off]
            rows.append((f"{mat_name} {dev_name}", "—"))

        self.samples_summary_table = ValueTable(rows, ("Sample", "Raw Value"))
        self.samples_summary_table.expand_to_contents()
        samples_panel.layout.addWidget(self.samples_summary_table)
        return samples_panel

    def build_telemetry_panel(self) -> Panel:
        telemetry_panel = Panel()
        header = QHBoxLayout()
        title = QLabel("TELEMETRY PARAMETERS")
        title.setObjectName("panelTitle")
        header.addWidget(title)
        header.addStretch(1)
        telemetry_panel.layout.addLayout(header)
        self.telemetry_table = ValueTable(
            [
                ("AD7177 Readings", "—"),
                ("Temperature Measurements", "—"),
                ("Heater Duty (permille)", "—"),
                ("Subsystem Health Indicators", "—"),
                ("Geiger 1 Valid", "—"),
                ("Geiger 1 Dose Rate (CPS)", "—"),
                ("Geiger 1 Total Dose (Sv)", "—"),
                ("Geiger 1 HV Voltage", "—"),
                ("Geiger 1 Error Flags", "—"),
                ("Geiger 2 Valid", "—"),
                ("Geiger 2 Dose Rate (CPS)", "—"),
                ("Geiger 2 Total Dose (Sv)", "—"),
                ("Geiger 2 HV Voltage", "—"),
                ("Geiger 2 Error Flags", "—"),
                ("Packet Timestamp (ms)", "—"),
                ("Health Code", "—"),
                ("Counter", "—"),
                ("Flags", "—"),
                ("Temperature Valid Mask", "—"),
                ("ADC Valid Mask", "—"),
                ("Source", "—"),
                ("Last Seen", "—"),
            ],
            ("Parameter", "Value"),
        )
        self.telemetry_table.expand_to_contents()
        telemetry_panel.layout.addWidget(self.telemetry_table)
        return telemetry_panel

    def build_command_panel(self) -> Panel:
        command_frame = Panel("COMMAND UPLINK")
        self.ping_button = QPushButton("Ping")
        self.status_button = QPushButton("Telemetry status")
        self.telemetry_on_button = QPushButton("Enable telemetry")
        self.telemetry_off_button = QPushButton("Disable telemetry")

        self.ping_button.clicked.connect(lambda: self.send_command(COMMAND_PING, 0))
        self.status_button.clicked.connect(lambda: self.send_command(COMMAND_TELEMETRY_STATUS, 0))
        self.telemetry_on_button.clicked.connect(
            lambda: self.send_command(COMMAND_TELEMETRY_SET, VALUE_ON)
        )
        self.telemetry_off_button.clicked.connect(
            lambda: self.send_command(COMMAND_TELEMETRY_SET, VALUE_OFF)
        )

        button_grid = QGridLayout()
        button_grid.setSpacing(8)
        button_grid.addWidget(self.ping_button, 0, 0)
        button_grid.addWidget(self.status_button, 0, 1)
        button_grid.addWidget(self.telemetry_on_button, 1, 0)
        button_grid.addWidget(self.telemetry_off_button, 1, 1)
        command_frame.layout.addLayout(button_grid)

        return command_frame

    def build_packet_panel(self) -> Panel:
        self.packet_table = ValueTable(
            [
                ("Magic Value", "CCTM"),
                ("Version", "—"),
                ("Message Type", "—"),
                ("Flags", "—"),
                ("Payload Length", "—"),
                ("Packet Timestamp (ms)", "—"),
                ("Counter", "—"),
                ("Health Code", "—"),
                ("Temperature Valid Mask", "—"),
                ("Temperature Sensors", "—"),
                ("ADC Valid Mask", "—"),
                ("AD7177 Readings", "—"),
                ("Geiger 1 Valid", "—"),
                ("Geiger 1 Error Flags", "—"),
                ("Geiger 1 Event ID", "—"),
                ("Geiger 1 Dose CPS", "—"),
                ("Geiger 1 Dose Rate CPS", "—"),
                ("Geiger 1 Total Dose Sv", "—"),
                ("Geiger 1 Dose Time Sec", "—"),
                ("Geiger 1 Stats Time Sec", "—"),
                ("Geiger 1 HV Voltage", "—"),
                ("Geiger 1 Stat Error %", "—"),
                ("Geiger 1 Stat Cell Count", "—"),
                ("Geiger 2 Valid", "—"),
                ("Geiger 2 Error Flags", "—"),
                ("Geiger 2 Event ID", "—"),
                ("Geiger 2 Dose CPS", "—"),
                ("Geiger 2 Dose Rate CPS", "—"),
                ("Geiger 2 Total Dose Sv", "—"),
                ("Geiger 2 Dose Time Sec", "—"),
                ("Geiger 2 Stats Time Sec", "—"),
                ("Geiger 2 HV Voltage", "—"),
                ("Geiger 2 Stat Error %", "—"),
                ("Geiger 2 Stat Cell Count", "—"),
                ("TCP Server", "—"),
            ]
        )
        self.packet_table.expand_to_contents()
        packet_panel = Panel("PACKET FIELDS")
        packet_panel.layout.addWidget(self.packet_table)
        return packet_panel

    def build_samples_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        toggle_row = QHBoxLayout()
        toggle_row.addStretch(1)
        self.samples_display_toggle = QPushButton("Showing: Voltage")
        self.samples_display_toggle.clicked.connect(self.toggle_samples_display_mode)
        toggle_row.addWidget(self.samples_display_toggle)
        layout.addLayout(toggle_row)

        materials = [
            ("TIPs-pentacene", 0),
            ("diF-TES-ADT", 1),
            ("Rubrene", 2),
        ]
        device_types = ["Device 1a", "Device 2a", "Device 1b", "Device 2b"]

        for slot in range(12):
            ch = slot % 3
            dev_off = slot // 3
            mat_name = materials[ch][0]
            name = f"{mat_name} {device_types[dev_off]}"
            card = SampleCard(name, slot)
            card.graph_requested.connect(self.show_adc_graph_dialog)
            self.sample_cards.append(card)

        for mat_name, ch in materials:
            mat_panel = Panel(mat_name.upper())
            grid = QGridLayout()
            grid.setSpacing(8)

            for slot, row, col in [
                (ch, 0, 0),
                (3 + ch, 0, 1),
                (6 + ch, 1, 0),
                (9 + ch, 1, 1),
            ]:
                card = self.sample_cards[slot]
                grid.addWidget(card, row, col)

            mat_panel.layout.addLayout(grid)
            layout.addWidget(mat_panel)

        layout.addStretch(1)
        return page

    def show_adc_graph_dialog(self, slot: int) -> None:
        try:
            if not 0 <= slot < len(self.sample_cards):
                return
            card = self.sample_cards[slot]
            adc_index = slot // AD7177_CHANNEL_COUNT
            channel_index = slot % AD7177_CHANNEL_COUNT
            title = f"{card.toggle_button.text()} / ADC{adc_index} CH{channel_index}"
            raw_mode = self.samples_display_mode == "raw"

            def points_fn(s=slot, raw_mode=raw_mode):
                rows = self.adc_db.query_adc(s, limit=5000)
                if raw_mode:
                    return [(received_wall, float(raw24)) for received_wall, raw24 in rows]
                return [(received_wall, raw24_to_volts(raw24)) for received_wall, raw24 in rows]

            self.show_plot_dialog(
                plot_id=f"adc_{slot}",
                title=title,
                y_label="Raw24" if raw_mode else "Volts (V)",
                hover_label="Raw24" if raw_mode else "Volts",
                points_fn=points_fn,
                latest_fn=lambda s=slot: f"{self.sample_cards[s].reading_label.text()} / {self.sample_cards[s].temperature_label.text()}",
            )
        except Exception as exc:
            self.log(f"Failed to open ADC graph: {exc}")

    def show_geiger_dialog(self, counter_id: int) -> None:
        try:
            name = "Geiger 1" if counter_id == 0 else "Geiger 2"
            self.show_plot_dialog(
                plot_id=f"geiger_{counter_id}",
                title=f"{name} DOSE RATE",
                y_label="Dose rate (CPS)",
                hover_label="CPS",
                points_fn=lambda cid=counter_id: list(self.adc_db.query_geiger(cid, limit=5000)),
                latest_fn=lambda: "",
            )
        except Exception as exc:
            self.log(f"Failed to open geiger graph: {exc}")

    def show_plot_dialog(
        self,
        plot_id: str,
        title: str,
        y_label: str,
        points_fn,
        latest_fn=None,
        hover_label: str | None = None,
    ) -> None:
        if plot_id in self.plot_dialogs:
            self.plot_dialogs[plot_id].raise_()
            self.plot_dialogs[plot_id].activateWindow()
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.resize(900, 520)

        frame = QFrame()
        frame.setObjectName("panel")
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(12, 12, 12, 12)
        frame_layout.setSpacing(8)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(frame)

        top_row = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("panelTitle")
        top_row.addWidget(title_label, 1)

        latest_label = QLabel(latest_fn() if latest_fn else "")
        latest_label.setObjectName("smallNote")
        latest_label.setWordWrap(True)

        plot = PlotWidget(y_label, "No data", absolute_time=True, hover_label=hover_label, y_range=(-150.0, 150.0) if "temperature" in y_label.lower() else None, min_y_range=5.0 if "temperature" in y_label.lower() else None, min_x_range=10.0 if "temperature" in y_label.lower() else None)
        plot.setMinimumHeight(400)

        frame_layout.addLayout(top_row)
        if latest_fn:
            frame_layout.addWidget(latest_label)
        frame_layout.addWidget(plot, 1)

        self.plot_dialogs[plot_id] = dialog
        self.plot_dialog_refs[plot_id] = {
            "plot": plot,
            "latest": latest_label,
            "points_fn": points_fn,
            "latest_fn": latest_fn,
        }

        dialog.finished.connect(lambda _result, pid=plot_id: self.clear_plot_dialog(pid))

        points = points_fn()
        plot.set_points(points)
        dialog.show()

    def clear_plot_dialog(self, plot_id: str) -> None:
        self.plot_dialogs.pop(plot_id, None)
        self.plot_dialog_refs.pop(plot_id, None)

    def update_plot_dialogs(self) -> None:
        for plot_id, refs in list(self.plot_dialog_refs.items()):
            plot: PlotWidget = refs["plot"]
            latest: QLabel = refs["latest"]
            points_fn = refs["points_fn"]
            latest_fn = refs["latest_fn"]

            if latest_fn:
                latest.setText(latest_fn())

            if plot_id.startswith("adc_"):
                raw_entries = self.telemetry_history.adc_points(int(plot_id.removeprefix("adc_")))
                points = [
                    adc_point_for_mode(entry, self.samples_display_mode) for entry in raw_entries
                ]
            elif plot_id.startswith("geiger_"):
                points = self.telemetry_history.geiger_points(
                    int(plot_id.removeprefix("geiger_"))
                )
            else:
                points = points_fn()
            plot.set_points(points)

    def build_temperature_page(self) -> QWidget:
        self.pid_page = PidPage()
        self.pid_page.target_requested.connect(self.set_pid_target)
        self.pid_page.gain_requested.connect(self.set_pid_gain)
        self.pid_page.manual_duty_requested.connect(self.set_pid_manual_duty)
        self.pid_page.return_pid_requested.connect(self.return_pid_heater)
        self.pid_page.all_off_requested.connect(self.turn_all_heaters_off)
        self.pid_page.all_pid_requested.connect(self.set_all_pid)
        return self.pid_page

    def build_health_page(self) -> QWidget:
        page = QWidget()
        self.health_page = page
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.health_table = ValueTable(
            [
                ("Command Connection", "Disconnected"),
                ("Telemetry Receiver", f"waiting on UDP {DEFAULT_TELEMETRY_PORT}"),
                ("Last Telemetry", "—"),
                ("Firmware Health", "—"),
                ("TCP Server", "—"),
                ("SD Temperature Logger", "—"),
                ("Temperature Sensors", "—"),
                ("AD7177 Channels", "—"),
                ("Geiger Detectors", "—"),
                ("Heater / PID Records", "—"),
            ],
            ("Subsystem", "State"),
        )
        self.health_table.expand_to_contents()

        self.health_geiger_table = ValueTable(
            [("Geiger 1", "—", "—", "unknown"), ("Geiger 2", "—", "—", "unknown")],
            ("Detector", "Dose rate", "HV", "State"),
        )
        self.health_geiger_table.expand_to_contents()

        self.health_temperature_table = ValueTable(
            [(f"TMP117-{index + 1}", "—", "unknown") for index in range(13)],
            ("Sensor", "Reading", "State"),
        )
        self.health_temperature_table.expand_to_contents()

        self.health_adc_table = ValueTable(
            [
                (f"ADC{index // 3} CH{index % 3}", "—", "unknown")
                for index in range(12)
            ],
            ("Channel", "Reading", "State"),
        )
        self.health_adc_table.expand_to_contents()

        self.health_heater_table = ValueTable(
            [(f"H{index}", SENSOR_NAMES[index], "—", "unknown") for index in range(12)],
            ("Heater", "Sensor", "Reading", "State"),
        )
        self.health_heater_table.expand_to_contents()

        self.system_health_section = HealthSummaryCard("SYSTEM")
        self.temperature_health_section = HealthSummaryCard("TEMPERATURE SENSORS")
        self.adc_health_section = HealthSummaryCard("SAMPLE CHANNELS")
        self.radiation_health_section = HealthSummaryCard("RADIATION DETECTORS")
        self.heater_health_section = HealthSummaryCard("HEATER / PID")
        self.health_details = {
            "System": (self.system_health_section, self.health_table),
            "Temperature Sensors": (
                self.temperature_health_section,
                self.health_temperature_table,
            ),
            "Sample Channels": (self.adc_health_section, self.health_adc_table),
            "Radiation Detectors": (
                self.radiation_health_section,
                self.health_geiger_table,
            ),
            "Heater / PID": (self.heater_health_section, self.health_heater_table),
        }

        health_grid = QGridLayout()
        health_grid.setSpacing(10)
        health_grid.setColumnStretch(0, 1)
        health_grid.setColumnStretch(1, 1)
        for index, (title, (card, _table)) in enumerate(self.health_details.items()):
            card.details_requested.connect(
                lambda title=title: self._show_health_details(title)
            )
            health_grid.addWidget(card, index // 2, index % 2)
        layout.addLayout(health_grid)

        self.health_detail_panel = Panel()
        detail_header = QHBoxLayout()
        detail_label = QLabel("DETAILS")
        detail_label.setObjectName("healthDetailLabel")
        self.health_detail_title = QLabel()
        self.health_detail_title.setObjectName("healthDetailTitle")
        detail_header.addWidget(detail_label)
        detail_header.addWidget(self.health_detail_title)
        detail_header.addStretch(1)
        self.health_detail_panel.layout.addLayout(detail_header)

        self.health_detail_stack = QStackedWidget()
        for _card, table in self.health_details.values():
            self.health_detail_stack.addWidget(table)
        self.health_detail_panel.layout.addWidget(self.health_detail_stack)
        layout.addWidget(self.health_detail_panel)
        self._show_health_details("System")
        layout.addStretch(1)
        return page

    def _show_health_details(self, title: str) -> None:
        selected_card, selected_table = self.health_details[title]
        self.health_detail_title.setText(title.upper())
        self.health_detail_stack.setCurrentWidget(selected_table)
        for card, _table in self.health_details.values():
            card.set_selected(card is selected_card)

    def build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        db_panel = Panel("ADC DATABASE")
        db_size = self.database_path.stat().st_size if self.database_path.exists() else 0
        self._db_info_label = QLabel(self._format_db_info(db_size, 0))
        self._db_info_label.setObjectName("smallNote")
        self._db_info_label.setWordWrap(True)
        db_panel.layout.addWidget(self._db_info_label)
        QTimer.singleShot(0, self._refresh_db_info)

        clear_btn = QPushButton("Archive database")
        clear_btn.clicked.connect(self._on_clear_db)
        db_panel.layout.addWidget(clear_btn)

        layout.addWidget(db_panel)
        layout.addStretch(1)
        return page

    def build_diagnostics_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        layout.addWidget(self.build_samples_summary_panel())
        layout.addWidget(self.build_telemetry_panel())
        layout.addWidget(self.build_command_panel())
        layout.addWidget(self.build_packet_panel())

        log_panel = Panel("OPERATOR LOG")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        self.log_view.setMinimumHeight(220)
        log_panel.layout.addWidget(self.log_view)
        layout.addWidget(log_panel)
        layout.addStretch(1)
        return page

    def _format_db_info(self, db_size: int, db_count: int) -> str:
        if db_size >= 1_048_576:
            size_str = f"{db_size / 1_048_576:.2f} MB"
        elif db_size > 0:
            size_str = f"{db_size:,} bytes"
        else:
            size_str = "N/A"
        return f"File: {self.database_path}\nRecords: {db_count:,}\nSize: {size_str}"

    def _refresh_db_info(self) -> None:
        db_size = self.database_path.stat().st_size if self.database_path.exists() else 0
        db_count = 0
        try:
            if self.adc_db.conn:
                cur = self.adc_db.conn.execute("SELECT COUNT(*) FROM adc")
                db_count = cur.fetchone()[0]
        except Exception:
            pass
        self._db_info_label.setText(self._format_db_info(db_size, db_count))

    def _on_clear_db(self) -> None:
        ret = QMessageBox.question(
            self, "Archive Database",
            "Archive the current database and start a new one? Existing data is preserved.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            self.adc_db.close()
            try:
                archive = archive_database(self.database_path)
            except (OSError, RuntimeError, sqlite3.Error) as exc:
                QMessageBox.critical(
                    self,
                    "Database Archive Failed",
                    f"The existing database was not replaced.\n\n{exc}",
                )
                self.adc_db = TelemetryDb(self.database_path, async_writes=True)
                self.telemetry_history.set_database(self.adc_db)
                return
            self.adc_db = TelemetryDb(self.database_path, async_writes=True)
            self.telemetry_history.set_database(self.adc_db)
            self._refresh_db_info()
            self.log(f"Database archived to {archive}, new database created")

    def switch_view(self, view: str) -> None:
        order = [
            VIEW_DASHBOARD,
            VIEW_RADIATION,
            VIEW_SAMPLES,
            VIEW_TEMPERATURE,
            VIEW_HEALTH,
            VIEW_DIAGNOSTICS,
            VIEW_SETTINGS,
        ]
        self.pages.setCurrentIndex(order.index(view))
        for name, button in self.view_buttons.items():
            button.setObjectName("navButtonActive" if name == view else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)
