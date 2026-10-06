from __future__ import annotations

import os
import sqlite3
from functools import partial

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
from ..plot_widget import HistoryPlotWidget, PlotWidget
from ..plot_history import PlotHistoryLoader
from ..health_model import evaluate_health, link_status
from ..sample_layout import SAMPLE_CHANNELS, SAMPLE_COLUMNS
from ..protocol import (
    AD7177_BIPOLAR_MIDSCALE,
    AD7177_VREF_VOLTS,
    COMMAND_GEIGER_CLEAR_HISTORY,
    COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
    COMMAND_GEIGER_RESET_STATS,
    COMMAND_PING,
    COMMAND_SYSTEM_RESET,
    COMMAND_TELEMETRY_SET,
    COMMAND_TELEMETRY_STATUS,
    DEFAULT_COMMAND_PORT,
    DEFAULT_DEVICE_HOST,
    VALUE_OFF,
    VALUE_ON,
)
from ..telemetry_db import TelemetryDb, archive_database
from ..telemetry_csv import CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY
from .board_map import BoardMapWidget
from .health_page import HealthPage
from .widgets import Panel, SampleCard, StatCard, ValueTable
from .status_widgets import StatusIndicator


def raw24_to_volts(raw24: int) -> float:
    return (raw24 - AD7177_BIPOLAR_MIDSCALE) / AD7177_BIPOLAR_MIDSCALE * AD7177_VREF_VOLTS


VIEW_DASHBOARD = "DASHBOARD"
VIEW_RADIATION = "RADIATION"
VIEW_SAMPLES = "SAMPLES"
VIEW_TEMPERATURE = "HEATING"
VIEW_HEALTH = "HEALTH"
VIEW_BOARDS = "BOARDS"
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
        self.samples_page_index = self.pages.addWidget(self.scroll_page(self.build_samples_page()))
        self.pages.addWidget(self.scroll_page(self.build_temperature_page()))
        self.pages.addWidget(self.scroll_page(self.build_health_page()))
        self.pages.addWidget(self.scroll_page(self.build_boards_page()))
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
            VIEW_BOARDS,
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
        self.monitoring_geiger_plot.on_double_click = lambda: self.show_geiger_dialog()
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

        rows = [
            (channel.name, "—")
            for _material, channels in SAMPLE_COLUMNS
            for channel in channels
        ]

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
                ("Average Heater Duty", "—"),
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

        self.sample_cards = [SampleCard(SAMPLE_CHANNELS[slot]) for slot in sorted(SAMPLE_CHANNELS)]
        for card in self.sample_cards:
            card.graph_requested.connect(self.show_adc_graph_dialog)

        # One column per material, channels top to bottom in ADC order.
        columns = QHBoxLayout()
        columns.setSpacing(12)
        for material, channels in SAMPLE_COLUMNS:
            material_panel = Panel(material.upper())
            for channel in channels:
                material_panel.layout.addWidget(self.sample_cards[channel.slot])
            columns.addWidget(material_panel, 1)
        layout.addLayout(columns)

        layout.addStretch(1)
        return page

    def show_adc_graph_dialog(self, slot: int) -> None:
        try:
            if not 0 <= slot < len(self.sample_cards):
                return
            channel = SAMPLE_CHANNELS[slot]
            title = f"{channel.name} / {channel.location}"
            raw_mode = self.samples_display_mode == "raw"

            self.show_plot_dialog(
                plot_id=f"adc_{slot}",
                title=title,
                y_label="Raw24" if raw_mode else "Volts (V)",
                hover_label="Raw24" if raw_mode else "Volts",
                history_sources=(("adc", slot, raw_mode),),
                latest_fn=lambda s=slot: f"{self.sample_cards[s].reading_label.text()} / {self.sample_cards[s].status_label.text()}",
            )
        except Exception as exc:
            self.log(f"Failed to open ADC graph: {exc}")

    def show_geiger_dialog(self, counter_id: int | None = None) -> None:
        try:
            counters = (0, 1) if counter_id is None else (counter_id,)
            name = "Geiger 1 + Geiger 2" if counter_id is None else f"Geiger {counter_id + 1}"
            self.show_plot_dialog(
                plot_id="geiger_all" if counter_id is None else f"geiger_{counter_id}",
                title=f"{name} DOSE RATE",
                y_label="Dose rate (CPS)",
                hover_label="CPS",
                history_sources=tuple(("geiger", counter, False) for counter in counters),
                series_names=tuple(f"Geiger {counter + 1}" for counter in counters),
                latest_fn=lambda: "",
            )
        except Exception as exc:
            self.log(f"Failed to open geiger graph: {exc}")

    def show_plot_dialog(
        self,
        plot_id: str,
        title: str,
        y_label: str,
        history_sources: tuple[tuple[str, int, bool], ...],
        latest_fn=None,
        hover_label: str | None = None,
        series_names: tuple[str, ...] = ("",),
    ) -> None:
        if plot_id in self.plot_dialogs:
            self.plot_dialogs[plot_id].raise_()
            self.plot_dialogs[plot_id].activateWindow()
            return

        dialog = QDialog(self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
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

        plot = HistoryPlotWidget(y_label, "Loading history…", hover_label=hover_label,
                                 series_names=series_names)
        plot.setMinimumHeight(400)

        history_status = QLabel("Loading full database history…")
        history_status.setObjectName("smallNote")
        fit_button = QPushButton("Fit full history")
        fit_button.clicked.connect(lambda: plot.enableAutoRange(x=True, y=True))
        top_row.addWidget(fit_button)
        loaders = []
        caught_up = set()

        def history_caught_up(index: int) -> None:
            caught_up.add(index)
            if len(caught_up) == len(history_sources):
                history_status.setText(f"Full history · {plot.sample_count:,} samples · live")
                plot._empty_label.setText("No recorded data")

        for index, source in enumerate(history_sources):
            loader = PlotHistoryLoader(self.database_path, *source, parent=dialog)
            loader.batch_ready.connect(partial(plot.append_history, series_index=index))
            loader.caught_up.connect(partial(history_caught_up, index))
            loader.failed.connect(lambda message: history_status.setText(f"History read failed: {message}"))
            loaders.append(loader)

        frame_layout.addLayout(top_row)
        if latest_fn:
            frame_layout.addWidget(latest_label)
        frame_layout.addWidget(plot, 1)
        frame_layout.addWidget(history_status)

        self.plot_dialogs[plot_id] = dialog
        self.plot_dialog_refs[plot_id] = {
            "plot": plot,
            "latest": latest_label,
            "loaders": loaders,
            "latest_fn": latest_fn,
        }

        dialog.finished.connect(lambda _result, pid=plot_id: self.clear_plot_dialog(pid))

        dialog.show()
        for loader in loaders:
            loader.start()

    def clear_plot_dialog(self, plot_id: str) -> None:
        self.plot_dialogs.pop(plot_id, None)
        refs = self.plot_dialog_refs.pop(plot_id, None)
        if refs is not None:
            for loader in refs["loaders"]:
                loader.close()

    def update_plot_dialogs(self) -> None:
        for refs in list(self.plot_dialog_refs.values()):
            latest: QLabel = refs["latest"]
            latest_fn = refs["latest_fn"]

            if latest_fn:
                latest.setText(latest_fn())

            # Historical plots fetch new committed rows on their own timer;
            # telemetry updates must never replace them with the rolling buffer.

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
        self.health_page = HealthPage(evaluate_health(None, None, link_status(False, None)))
        return self.health_page

    def build_boards_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        panel = Panel("BOARD MAP")
        self.board_map = BoardMapWidget()
        panel.layout.addWidget(self.board_map)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

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

        danger_panel = Panel("DANGER ZONE")
        danger_note = QLabel(
            "Reboots the flight computer over the uplink. All sensors, the ADCs, "
            "and the network stack reinitialize from scratch; in-flight state is lost."
        )
        danger_note.setObjectName("smallNote")
        danger_note.setWordWrap(True)
        danger_panel.layout.addWidget(danger_note)

        reset_btn = QPushButton("Reset board")
        reset_btn.setObjectName("dangerButton")
        reset_btn.clicked.connect(self._on_reset_board)
        danger_panel.layout.addWidget(reset_btn)

        layout.addWidget(danger_panel)
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
            "Archive the current database and start a new one? Existing data is "
            "preserved in the archive, and all plots are cleared.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            for dialog in list(self.plot_dialogs.values()):
                dialog.close()
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
            self.clear_session_history()
            self._refresh_db_info()
            self.log(f"Database archived to {archive}, new database created")

    def clear_session_history(self) -> None:
        """Empty every rolling plot so the new database starts from a clean slate."""
        self.telemetry_history.clear()
        self._last_adc_packet = None
        self._last_adc_history = None
        for plot in (
            self.monitoring_geiger_plot,
            self.monitoring_adc_average_plot,
            self.radiation_geiger_plot,
            self.radiation_geiger_2_plot,
            *(card.plot for card in self.sample_cards),
        ):
            plot.set_points([])
        self.radiation_plot_status.setText("0/300 points")
        self.radiation_2_plot_status.setText("0/300 points")
        self.session_frame_count_card.set_value("0")
        self.pid_page.clear_history()

    def _on_reset_board(self) -> None:
        ret = QMessageBox.question(
            self, "Reset Board",
            "This will reboot the flight computer right now. The uplink will "
            "drop and telemetry will pause for several seconds while it "
            "reinitializes. Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            self.log("Sending reset command")
            self.send_command(COMMAND_SYSTEM_RESET, 0)

    def switch_view(self, view: str) -> None:
        order = [
            VIEW_DASHBOARD,
            VIEW_RADIATION,
            VIEW_SAMPLES,
            VIEW_TEMPERATURE,
            VIEW_HEALTH,
            VIEW_BOARDS,
            VIEW_DIAGNOSTICS,
            VIEW_SETTINGS,
        ]
        self.pages.setCurrentIndex(order.index(view))
        if view == VIEW_SAMPLES:
            # Sample plots are only refreshed while visible; catch up now.
            self.refresh_sample_cards()
        for name, button in self.view_buttons.items():
            button.setObjectName("navButtonActive" if name == view else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)
