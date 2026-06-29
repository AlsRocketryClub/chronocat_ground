from __future__ import annotations

from collections import deque
from datetime import datetime
import time

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .command_client import CommandClient
from .protocol import (
    COMMAND_PING,
    COMMAND_TELEMETRY_SET,
    COMMAND_TELEMETRY_STATUS,
    DEFAULT_COMMAND_PORT,
    DEFAULT_DEVICE_HOST,
    DEFAULT_TELEMETRY_PORT,
    GEIGER_ERROR_NAMES,
    VALUE_OFF,
    VALUE_ON,
    CommandResponse,
    TelemetryPacket,
    command_name,
    status_name,
    telemetry_health_name,
    telemetry_value_name,
    tcp_status_name,
)
from .telemetry_csv import TelemetryCsvLogger
from .telemetry_receiver import TelemetryReceiver


VIEW_MONITORING = "MONITORING"
VIEW_RADIATION = "RADIATION"
VIEW_SAMPLES = "SAMPLES"
VIEW_TEMPERATURE = "TEMPERATURE MEASUREMENTS"
VIEW_HEALTH = "SUBSYSTEM HEALTH"
VIEW_HISTORY = "HISTORIC FLIGHT DATA"


class Panel(QFrame):
    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.layout.setSpacing(8)

        if title is not None:
            title_label = QLabel(title)
            title_label.setObjectName("panelTitle")
            self.layout.addWidget(title_label)


class StatCard(QFrame):
    def __init__(self, title: str, subtitle: str = "", value: str = "X") -> None:
        super().__init__()
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("kpiLabel")

        self.value_label = QLabel(value)
        self.value_label.setObjectName("kpiValue")
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("kpiSub")
        self.subtitle_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(4)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.subtitle_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class ValueTable(QTableWidget):
    def __init__(self, rows: list[tuple[str, str]], headers: tuple[str, str] | None = None) -> None:
        super().__init__(len(rows), 2)
        self.setObjectName("dataTable")
        self.setShowGrid(True)
        self.setAlternatingRowColors(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        if headers is None:
            self.horizontalHeader().setVisible(False)
        else:
            self.setHorizontalHeaderLabels(headers)

        for row_index, (name, value) in enumerate(rows):
            self.setItem(row_index, 0, QTableWidgetItem(name))
            self.setItem(row_index, 1, QTableWidgetItem(value))

        self.resizeRowsToContents()

    def set_value(self, name: str, value: str) -> None:
        for row in range(self.rowCount()):
            item = self.item(row, 0)
            if item is not None and item.text() == name:
                value_item = self.item(row, 1)
                if value_item is not None:
                    value_item.setText(value)
                return


class LinePlotWidget(QWidget):
    def __init__(self, y_label: str) -> None:
        super().__init__()
        self.setObjectName("linePlot")
        self.setMinimumHeight(180)
        self.points: list[tuple[float, float]] = []
        self.y_label = y_label

    def set_points(self, points: list[tuple[float, float]]) -> None:
        self.points = points
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        bounds = self.rect()
        painter.fillRect(bounds, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#b0b0b0"), 1))
        painter.drawRect(bounds.adjusted(0, 0, -1, -1))

        plot = QRectF(54, 18, max(10, bounds.width() - 72), max(10, bounds.height() - 48))
        painter.setPen(QPen(QColor("#d0d0d0"), 1))
        for index in range(1, 4):
            y = plot.top() + plot.height() * index / 4
            painter.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))
        for index in range(1, 5):
            x = plot.left() + plot.width() * index / 5
            painter.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))

        painter.setPen(QPen(QColor("#111111"), 1))
        painter.drawText(8, 16, self.y_label)
        painter.drawText(int(plot.right()) - 26, bounds.height() - 8, "time")

        if len(self.points) < 2:
            painter.drawText(plot, Qt.AlignCenter, "Waiting for Geiger telemetry")
            return

        x_values = [point[0] for point in self.points]
        y_values = [point[1] for point in self.points]
        min_x = min(x_values)
        max_x = max(x_values)
        min_y = min(y_values)
        max_y = max(y_values)

        if max_x == min_x:
            max_x = min_x + 1
        if max_y == min_y:
            max_y = min_y + 1

        painter.setPen(QPen(QColor("#444444"), 1))
        painter.drawText(8, int(plot.top()) + 8, f"{max_y:.3g}")
        painter.drawText(8, int(plot.bottom()), f"{min_y:.3g}")

        polyline = QPolygonF()
        for x_value, y_value in self.points:
            x = plot.left() + ((x_value - min_x) / (max_x - min_x)) * plot.width()
            y = plot.bottom() - ((y_value - min_y) / (max_y - min_y)) * plot.height()
            polyline.append(QPointF(x, y))

        painter.setPen(QPen(QColor("#111111"), 2))
        painter.drawPolyline(polyline)


class SampleCard(QFrame):
    def __init__(self, circuit: int, pair: int, sample_name: str) -> None:
        super().__init__()
        self.setObjectName("sampleCard")
        self.details_visible = False

        self.toggle_button = QPushButton(sample_name)
        self.toggle_button.setObjectName("sampleToggle")
        self.toggle_button.clicked.connect(self.toggle_details)

        self.reading_label = QLabel("Newest reading: X")
        self.reading_label.setObjectName("sampleMetric")
        self.temperature_label = QLabel("Temperature: X °C")
        self.temperature_label.setObjectName("sampleMetric")
        self.meta_label = QLabel(f"Measurement circuit {circuit} / Pair {pair}")
        self.meta_label.setObjectName("smallNote")

        self.detail_panel = QFrame()
        self.detail_panel.setObjectName("chartBox")
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(8, 8, 8, 8)
        chart_title = QLabel("TIME-SERIES DATA")
        chart_title.setObjectName("panelTitle")
        chart = QLabel("X\n\n  /--\\____/---\\____/--\\\n\nTIME")
        chart.setObjectName("sampleChart")
        chart.setAlignment(Qt.AlignCenter)
        detail_layout.addWidget(chart_title)
        detail_layout.addWidget(chart)
        self.detail_panel.hide()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.reading_label)
        layout.addWidget(self.temperature_label)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.detail_panel)

    def toggle_details(self) -> None:
        self.details_visible = not self.details_visible
        self.detail_panel.setVisible(self.details_visible)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("chronocat_ground")
        self.resize(1180, 760)
        self.setMinimumSize(760, 520)

        self.client = CommandClient()
        self.telemetry_count = 0
        self.last_telemetry_time: float | None = None
        self.view_buttons: dict[str, QPushButton] = {}
        self.geiger_dose_rate_history: deque[tuple[float, float]] = deque(maxlen=300)
        self.csv_logger: TelemetryCsvLogger | None = None

        self.telemetry_receiver = TelemetryReceiver(DEFAULT_TELEMETRY_PORT)
        self.telemetry_receiver.packet_received.connect(self.on_telemetry_packet)
        self.telemetry_receiver.receive_error.connect(self.log)

        self.age_timer = QTimer(self)
        self.age_timer.timeout.connect(self.update_telemetry_age)
        self.age_timer.start(250)

        self.setCentralWidget(self.build_ui())
        self.apply_style()
        self.switch_view(VIEW_MONITORING)
        self.update_connection_state()
        self.telemetry_receiver.start()
        self.log(f"Listening for UDP telemetry on port {DEFAULT_TELEMETRY_PORT}")

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
        self.pages.addWidget(self.scroll_page(self.build_monitoring_page()))
        self.pages.addWidget(self.scroll_page(self.build_radiation_page()))
        self.pages.addWidget(self.scroll_page(self.build_samples_page()))
        self.pages.addWidget(self.scroll_page(self.build_temperature_page()))
        self.pages.addWidget(self.scroll_page(self.build_health_page()))
        self.pages.addWidget(self.scroll_page(self.build_history_page()))
        body.addWidget(self.pages, 1)

        return root

    def build_topbar(self) -> QFrame:
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(12, 12, 12, 12)
        topbar_layout.setSpacing(16)

        title_box = QVBoxLayout()
        title_box.setSpacing(4)
        eyebrow = QLabel("BEXUS 39 / CHRONO-CAT")
        eyebrow.setObjectName("eyebrow")
        header = QLabel("GROUND STATION SOFTWARE")
        header.setObjectName("title")
        subtitle = QLabel("Real-time monitoring, command uplink, telemetry, and historic flight data analysis")
        subtitle.setObjectName("subtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(eyebrow)
        title_box.addWidget(header)
        title_box.addWidget(subtitle)
        topbar_layout.addLayout(title_box, 1)

        self.host_input = QLineEdit(DEFAULT_DEVICE_HOST)
        self.host_input.setPlaceholderText("Device IP")
        self.host_input.setMinimumWidth(120)

        self.port_input = QLineEdit(str(DEFAULT_COMMAND_PORT))
        self.port_input.setPlaceholderText("Port")
        self.port_input.setMaximumWidth(80)

        self.connection_label = QLabel("Disconnected")
        self.connection_label.setObjectName("statusPill")

        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)

        self.csv_log_button = QPushButton("Start CSV Log")
        self.csv_log_button.clicked.connect(self.toggle_csv_logging)
        self.csv_log_status = QLabel("CSV logging off")
        self.csv_log_status.setObjectName("smallNote")

        connection_bar = QHBoxLayout()
        connection_bar.setSpacing(8)
        connection_bar.addWidget(QLabel("Host"))
        connection_bar.addWidget(self.host_input)
        connection_bar.addWidget(QLabel("Port"))
        connection_bar.addWidget(self.port_input)
        connection_bar.addWidget(self.connection_label)
        connection_bar.addWidget(self.connect_button)
        connection_bar.addWidget(self.csv_log_button)
        connection_bar.addWidget(self.csv_log_status)
        topbar_layout.addLayout(connection_bar)

        return topbar

    def build_sidebar(self) -> Panel:
        sidebar = Panel("MISSION PHASES")
        sidebar.setObjectName("sidebar")
        sidebar.setMinimumWidth(160)
        sidebar.setMaximumWidth(210)

        prelaunch_button = QPushButton("PRE-LAUNCH TESTING")
        prelaunch_button.setObjectName("navButtonActive")
        flight_button = QPushButton("FLIGHT")
        flight_button.setObjectName("navButton")
        sidebar.layout.addWidget(prelaunch_button)
        sidebar.layout.addWidget(flight_button)

        views_title = QLabel("VIEWS")
        views_title.setObjectName("panelTitle")
        sidebar.layout.addSpacing(8)
        sidebar.layout.addWidget(views_title)

        for view in (VIEW_MONITORING, VIEW_RADIATION, VIEW_SAMPLES, VIEW_TEMPERATURE, VIEW_HEALTH, VIEW_HISTORY):
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

    def build_monitoring_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.telemetry_state = QLabel("Waiting for telemetry")
        self.telemetry_state.setObjectName("telemetryState")
        layout.addWidget(self.telemetry_state)

        self.seq_card = StatCard("PACKET COUNTER", "received sequence number")
        self.tick_card = StatCard("PACKET TIMESTAMP", "firmware tick count, 10 ms units")
        self.tcp_card = StatCard("HEALTH CODE", "TCP server state")
        self.count_card = StatCard("TELEMETRY STORAGE", "packets received this session")
        self.geiger_dose_rate_card = StatCard("GEIGER DOSE RATE", "dose rate, CPS")
        self.geiger_total_dose_card = StatCard("GEIGER TOTAL DOSE", "accumulated dose, Sv")
        self.geiger_hv_card = StatCard("GEIGER HV", "HV voltage")
        self.geiger_errors_card = StatCard("GEIGER ERRORS", "error flags")

        cards = QGridLayout()
        cards.setSpacing(12)
        cards.addWidget(self.seq_card, 0, 0)
        cards.addWidget(self.tick_card, 0, 1)
        cards.addWidget(self.tcp_card, 1, 0)
        cards.addWidget(self.count_card, 1, 1)
        cards.addWidget(self.geiger_dose_rate_card, 2, 0)
        cards.addWidget(self.geiger_total_dose_card, 2, 1)
        cards.addWidget(self.geiger_hv_card, 3, 0)
        cards.addWidget(self.geiger_errors_card, 3, 1)
        layout.addLayout(cards)

        layout.addWidget(self.build_chart_panel())

        tables = QGridLayout()
        tables.setSpacing(12)
        tables.addWidget(self.build_samples_summary_panel(), 0, 0)
        tables.addWidget(self.build_telemetry_panel(), 0, 1)
        layout.addLayout(tables)

        lower = QGridLayout()
        lower.setSpacing(12)
        lower.addWidget(self.build_command_panel(), 0, 0)
        lower.addWidget(self.build_packet_panel(), 0, 1)
        layout.addLayout(lower)

        log_panel = Panel("OPERATOR LOG")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        log_panel.layout.addWidget(self.log_view)
        layout.addWidget(log_panel, 1)

        return page

    def build_chart_panel(self) -> Panel:
        chart_panel = Panel()
        chart_header = QHBoxLayout()
        chart_title = QLabel("GEIGER DOSE RATE TIME-SERIES")
        chart_title.setObjectName("panelTitle")
        self.timestamp_label = QLabel("RECEPTION TIMESTAMP: X")
        self.timestamp_label.setObjectName("smallNote")
        chart_header.addWidget(chart_title)
        chart_header.addStretch(1)
        chart_header.addWidget(self.timestamp_label)
        chart_panel.layout.addLayout(chart_header)
        self.monitoring_geiger_plot = LinePlotWidget("dose rate CPS")
        chart_panel.layout.addWidget(self.monitoring_geiger_plot)
        return chart_panel

    def build_radiation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        cards = QGridLayout()
        cards.setSpacing(12)
        self.radiation_dose_rate_card = StatCard("GEIGER DOSE RATE", "dose rate, CPS")
        self.radiation_total_dose_card = StatCard("GEIGER TOTAL DOSE", "accumulated dose, Sv")
        self.radiation_hv_card = StatCard("GEIGER HV", "HV voltage")
        self.radiation_errors_card = StatCard("GEIGER ERRORS", "error flags")
        cards.addWidget(self.radiation_dose_rate_card, 0, 0)
        cards.addWidget(self.radiation_total_dose_card, 0, 1)
        cards.addWidget(self.radiation_hv_card, 1, 0)
        cards.addWidget(self.radiation_errors_card, 1, 1)
        layout.addLayout(cards)

        plot_panel = Panel()
        plot_header = QHBoxLayout()
        title = QLabel("GEIGER DOSE RATE TIME-SERIES")
        title.setObjectName("panelTitle")
        self.radiation_plot_status = QLabel("300 point rolling window")
        self.radiation_plot_status.setObjectName("smallNote")
        plot_header.addWidget(title)
        plot_header.addStretch(1)
        plot_header.addWidget(self.radiation_plot_status)
        plot_panel.layout.addLayout(plot_header)
        self.radiation_geiger_plot = LinePlotWidget("dose rate CPS")
        self.radiation_geiger_plot.setMinimumHeight(320)
        plot_panel.layout.addWidget(self.radiation_geiger_plot)
        layout.addWidget(plot_panel)

        self.radiation_table = ValueTable(
            [
                ("Geiger Valid", "X"),
                ("Geiger Event ID", "X"),
                ("Geiger Dose CPS", "X"),
                ("Geiger Dose Rate CPS", "X"),
                ("Geiger Total Dose Sv", "X"),
                ("Geiger Dose Time Sec", "X"),
                ("Geiger Stats Time Sec", "X"),
                ("Geiger HV Voltage", "X"),
                ("Geiger Stat Error %", "X"),
                ("Geiger Stat Cell Count", "X"),
                ("Geiger Error Flags", "X"),
            ],
            ("Metric", "Value"),
        )
        table_panel = Panel("GEIGER PACKET DETAILS")
        table_panel.layout.addWidget(self.radiation_table)
        layout.addWidget(table_panel)
        layout.addStretch(1)
        return page

    def build_samples_summary_panel(self) -> Panel:
        samples_panel = Panel()
        header = QHBoxLayout()
        title = QLabel("SAMPLES")
        title.setObjectName("panelTitle")
        note = QLabel("12 samples across two measurement circuits")
        note.setObjectName("smallNote")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(note)
        samples_panel.layout.addLayout(header)
        samples_panel.layout.addWidget(
            ValueTable(
                [(f"Material #{index}", "X / X °C") for index in range(1, 7)]
                + [(f"Material #{index} shielded", "X / X °C") for index in range(1, 7)],
                ("Sample", "Reading / Temperature"),
            )
        )
        return samples_panel

    def build_telemetry_panel(self) -> Panel:
        telemetry_panel = Panel()
        header = QHBoxLayout()
        title = QLabel("TELEMETRY PARAMETERS")
        title.setObjectName("panelTitle")
        note = QLabel("Displayed as received from E-Link telemetry")
        note.setObjectName("smallNote")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(note)
        telemetry_panel.layout.addLayout(header)
        self.telemetry_table = ValueTable(
            [
                ("Organic Semiconductor ADC Readings", "X"),
                ("Temperature Measurements", "X"),
                ("Subsystem Health Indicators", "X"),
                ("Geiger Valid", "X"),
                ("Geiger Dose Rate (CPS)", "X"),
                ("Geiger Total Dose (Sv)", "X"),
                ("Geiger HV Voltage", "X"),
                ("Geiger Error Flags", "X"),
                ("Packet Timestamp", "X"),
                ("Health Code", "X"),
                ("Counter", "X"),
                ("Flags", "X"),
                ("Temperature Valid Mask", "X"),
                ("OS ADC Valid Mask", "X"),
                ("Source", "X"),
                ("Last Seen", "X"),
            ],
            ("Parameter", "Value"),
        )
        telemetry_panel.layout.addWidget(self.telemetry_table)
        return telemetry_panel

    def build_command_panel(self) -> Panel:
        command_frame = Panel("COMMAND UPLINK")
        rate_label = QLabel("Telemetry downlink rate configuration")
        rate_label.setObjectName("formLabel")
        rate_value = QLabel("X")
        rate_value.setObjectName("selectPlaceholder")
        command_frame.layout.addWidget(rate_label)
        command_frame.layout.addWidget(rate_value)

        self.ping_button = QPushButton("Ping")
        self.status_button = QPushButton("Status")
        self.telemetry_on_button = QPushButton("Telemetry On")
        self.telemetry_off_button = QPushButton("Telemetry Off")

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

        note = QLabel(
            "Uplink actions require a command server connection. Command acknowledgements are logged and displayed to the operator."
        )
        note.setObjectName("noteBox")
        note.setWordWrap(True)
        command_frame.layout.addWidget(note)
        return command_frame

    def build_packet_panel(self) -> Panel:
        self.packet_table = ValueTable(
            [
                ("Magic Value", "CCTM"),
                ("Version", "X"),
                ("Message Type", "X"),
                ("Flags", "X"),
                ("Payload Length", "X"),
                ("Packet Timestamp", "X"),
                ("Counter", "X"),
                ("Health Code", "X"),
                ("Temperature Valid Mask", "X"),
                ("Temperature Sensors", "X"),
                ("OS ADC Valid Mask", "X"),
                ("Organic Semiconductor ADC Readings", "X"),
                ("Geiger Valid", "X"),
                ("Geiger Error Flags", "X"),
                ("Geiger Event ID", "X"),
                ("Geiger Dose CPS", "X"),
                ("Geiger Dose Rate CPS", "X"),
                ("Geiger Total Dose Sv", "X"),
                ("Geiger Dose Time Sec", "X"),
                ("Geiger Stats Time Sec", "X"),
                ("Geiger HV Voltage", "X"),
                ("Geiger Stat Error %", "X"),
                ("Geiger Stat Cell Count", "X"),
                ("TCP Server", "X"),
            ]
        )
        packet_panel = Panel("PACKET FIELDS")
        packet_panel.layout.addWidget(self.packet_table)
        return packet_panel

    def build_samples_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        intro = Panel("SAMPLES")
        text = QLabel(
            "Newest placeholder readings for 12 samples. Each measurement circuit contains three material pairs; each pair has a sample and shielded sample. Click a sample to expand its time-series diagram."
        )
        text.setWordWrap(True)
        intro.layout.addWidget(text)
        layout.addWidget(intro)

        sample_index = 1
        for circuit in (1, 2):
            circuit_panel = Panel(f"MEASUREMENT CIRCUIT {circuit}")
            grid = QGridLayout()
            grid.setSpacing(8)
            for pair in range(1, 4):
                sample = SampleCard(circuit, pair, f"Material #{sample_index}")
                shielded = SampleCard(circuit, pair, f"Material #{sample_index} shielded")
                grid.addWidget(sample, pair - 1, 0)
                grid.addWidget(shielded, pair - 1, 1)
                sample_index += 1
            circuit_panel.layout.addLayout(grid)
            layout.addWidget(circuit_panel)

        layout.addStretch(1)
        return page

    def build_temperature_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        panel = Panel("TEMPERATURE MEASUREMENTS")
        self.temperature_table = ValueTable(
            [(f"Temperature sensor {index}", "X") for index in range(1, 14)],
            ("Sensor", "Newest Temperature"),
        )
        panel.layout.addWidget(self.temperature_table)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def build_health_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.health_table = ValueTable(
            [
                ("Command Connection", "Disconnected"),
                ("Telemetry Receiver", f"UDP {DEFAULT_TELEMETRY_PORT}"),
                ("TCP Server", "X"),
                ("Flags", "X"),
                ("Health Code", "X"),
                ("Packets Received", "0"),
                ("Last Telemetry", "X"),
            ],
            ("Subsystem", "State"),
        )
        panel = Panel("SUBSYSTEM HEALTH")
        panel.layout.addWidget(self.health_table)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def build_history_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        panel = Panel("HISTORIC FLIGHT DATA")
        meta = QLabel("Browse and analyse stored telemetry without onboard storage access. File loading is not implemented yet.")
        meta.setObjectName("smallNote")
        meta.setWordWrap(True)
        button = QPushButton("OPEN HISTORIC DATA")
        button.setEnabled(False)
        panel.layout.addWidget(meta)
        panel.layout.addWidget(button)
        layout.addWidget(panel)
        layout.addStretch(1)
        return page

    def switch_view(self, view: str) -> None:
        order = [VIEW_MONITORING, VIEW_RADIATION, VIEW_SAMPLES, VIEW_TEMPERATURE, VIEW_HEALTH, VIEW_HISTORY]
        self.pages.setCurrentIndex(order.index(view))
        for name, button in self.view_buttons.items():
            button.setObjectName("navButtonActive" if name == view else "navButton")
            button.style().unpolish(button)
            button.style().polish(button)

    def apply_style(self) -> None:
        QApplication.instance().setStyleSheet(
            """
            QWidget {
                color: #111111;
                font-family: Arial, Helvetica, sans-serif;
                font-size: 13px;
            }
            QLabel {
                background: transparent;
            }
            #appShell, #pages, #pageScroll, QScrollArea > QWidget > QWidget {
                background: #dcdcdc;
            }
            #topbar {
                background: #f4f4f4;
                border: 1px solid #8f8f8f;
            }
            #sidebar, #panel, #telemetryState, #sampleCard {
                background: #f8f8f8;
                border: 1px solid #7a7a7a;
                border-radius: 0px;
            }
            #eyebrow {
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            #title {
                font-size: 24px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            #panelTitle, #kpiLabel, #formLabel {
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            #kpiValue {
                font-size: 24px;
                font-weight: 700;
            }
            #subtitle, #kpiSub, #smallNote, #sampleMetric {
                color: #444444;
                font-size: 12px;
            }
            QLineEdit, QPlainTextEdit {
                background: #ffffff;
                border: 1px solid #777777;
                border-radius: 0px;
                padding: 6px 8px;
                selection-background-color: #d9d9d9;
                selection-color: #111111;
            }
            QPushButton {
                background: #ececec;
                border: 1px solid #666666;
                border-radius: 0px;
                padding: 8px 10px;
                font-weight: 700;
            }
            QPushButton:hover {
                background: #dfdfdf;
            }
            QPushButton:pressed {
                background: #d0d0d0;
            }
            QPushButton:disabled {
                color: #777777;
                background: #eeeeee;
                border-color: #aaaaaa;
            }
            #navButton, #navButtonActive {
                text-align: left;
                background: #ffffff;
                border: 1px solid #7f7f7f;
                font-weight: 400;
            }
            #navButtonActive {
                background: #d9d9d9;
                font-weight: 700;
            }
            #sampleToggle {
                text-align: left;
                background: #ffffff;
            }
            #statusPill {
                border: 1px solid #777777;
                border-radius: 0px;
                padding: 6px 10px;
                background: #edd0d0;
                font-weight: 700;
            }
            #statusPill[connected="true"] {
                background: #d8ead8;
            }
            #telemetryState {
                padding: 10px;
                font-weight: 700;
            }
            #telemetryState[active="true"] {
                background: #eef6ee;
            }
            #logView {
                font-family: SF Mono, Menlo, Consolas, monospace;
                font-size: 12px;
            }
            #dataTable {
                background: #ffffff;
                border: 1px solid #b5b5b5;
                gridline-color: #b5b5b5;
            }
            QTableWidget::item {
                background: #ffffff;
                padding: 4px 6px;
            }
            QHeaderView::section {
                background: #e7e7e7;
                border: 1px solid #b5b5b5;
                padding: 5px 6px;
                font-weight: 700;
            }
            #linePlot, #sparkline, #sampleChart, #chartBox {
                background: #ffffff;
                border: 1px solid #b0b0b0;
            }
            #linePlot {
                min-height: 180px;
            }
            #sparkline {
                min-height: 130px;
                font-family: SF Mono, Menlo, Consolas, monospace;
                font-size: 16px;
            }
            #sampleChart {
                min-height: 90px;
                font-family: SF Mono, Menlo, Consolas, monospace;
            }
            #selectPlaceholder, #noteBox {
                background: #ffffff;
                border: 1px solid #777777;
                padding: 8px;
            }
            #noteBox {
                background: #f1f1f1;
                font-size: 12px;
            }
            """
        )

    def toggle_connection(self) -> None:
        if self.client.connected:
            self.client.disconnect()
            self.log("Disconnected from command server")
            self.update_connection_state()
            return

        host = self.host_input.text().strip()
        try:
            port = int(self.port_input.text().strip())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self.log("Connect failed: port must be a number from 1 to 65535")
            return

        try:
            self.client.connect(host, port)
        except OSError as exc:
            self.log(f"Connect failed to {host}:{port}: {exc}")
            self.log("Check that the board is flashed, linked, and reachable at this IP.")
        else:
            self.log(f"Connected to {self.client.host}:{self.client.port}")

        self.update_connection_state()

    def toggle_csv_logging(self) -> None:
        if self.csv_logger is not None and self.csv_logger.active:
            self.stop_csv_logging()
            return

        self.csv_logger = TelemetryCsvLogger()
        try:
            self.csv_logger.start()
        except OSError as exc:
            self.csv_logger = None
            self.csv_log_status.setText("CSV logging failed")
            self.log(f"CSV logging failed: {exc}")
            return

        self.csv_log_button.setText("Stop CSV Log")
        self.csv_log_status.setText(f"CSV: {self.csv_logger.path.name} (0)")
        self.log(f"CSV logging started: {self.csv_logger.path}")

    def stop_csv_logging(self) -> None:
        if self.csv_logger is None:
            return
        path = self.csv_logger.path
        packet_count = self.csv_logger.packet_count
        self.csv_logger.stop()
        self.csv_logger = None
        self.csv_log_button.setText("Start CSV Log")
        self.csv_log_status.setText(f"CSV logging stopped ({packet_count})")
        self.log(f"CSV logging stopped: {path} ({packet_count} packet(s))")

    def update_connection_state(self) -> None:
        connected = self.client.connected
        self.connection_label.setText("Connected" if connected else "Disconnected")
        self.connection_label.setProperty("connected", connected)
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)
        self.connect_button.setText("Disconnect" if connected else "Connect")

        self.health_table.set_value("Command Connection", "Connected" if connected else "Disconnected")

        for button in (
            self.ping_button,
            self.status_button,
            self.telemetry_on_button,
            self.telemetry_off_button,
        ):
            button.setEnabled(connected)

    def send_command(self, command: int, value: int) -> None:
        try:
            response = self.client.send_command(command, value)
        except (OSError, ValueError, ConnectionError) as exc:
            self.log(f"Command failed: {exc}")
            self.client.disconnect()
            self.update_connection_state()
            return

        self.log_response(response)

    def log_response(self, response: CommandResponse) -> None:
        text = (
            f"Response {status_name(response.status)} "
            f"command={command_name(response.command)} "
            f"arg1={telemetry_value_name(response.arg1)} arg2={response.arg2}"
        )
        self.log(text)

    def on_telemetry_packet(self, packet: TelemetryPacket, source: str) -> None:
        self.telemetry_count += 1
        self.last_telemetry_time = time.monotonic()

        self.telemetry_state.setText("Telemetry receiving")
        self.telemetry_state.setProperty("active", True)
        self.telemetry_state.style().unpolish(self.telemetry_state)
        self.telemetry_state.style().polish(self.telemetry_state)

        tcp_state = tcp_status_name(packet.tcp_status)
        health = telemetry_health_name(packet.health_code)
        flags = f"0x{packet.flags:04x}"
        temp_summary = self.format_temperature_summary(packet)
        adc_summary = self.format_adc_summary(packet)

        self.seq_card.set_value(str(packet.counter))
        self.tick_card.set_value(str(packet.timestamp))
        self.tcp_card.set_value(health)
        self.count_card.set_value(str(self.telemetry_count))
        self.geiger_dose_rate_card.set_value(f"{packet.geiger_dose_rate_cps:.9g}")
        self.geiger_total_dose_card.set_value(f"{packet.geiger_total_dose_sv:.9g}")
        self.geiger_hv_card.set_value(str(packet.geiger_hv_voltage))
        geiger_error_str = ", ".join(
            name for mask, name in GEIGER_ERROR_NAMES.items()
            if mask and (packet.geiger_error_flags & mask)
        ) if packet.geiger_error_flags else "ok"
        self.geiger_errors_card.set_value(geiger_error_str)
        self.radiation_dose_rate_card.set_value(f"{packet.geiger_dose_rate_cps:.9g}")
        self.radiation_total_dose_card.set_value(f"{packet.geiger_total_dose_sv:.9g}")
        self.radiation_hv_card.set_value(str(packet.geiger_hv_voltage))
        self.radiation_errors_card.set_value(geiger_error_str)
        self.timestamp_label.setText(f"RECEPTION TIMESTAMP: {datetime.now().strftime('%H:%M:%S')}")

        if packet.geiger_valid:
            self.geiger_dose_rate_history.append((float(packet.timestamp), packet.geiger_dose_rate_cps))
            points = list(self.geiger_dose_rate_history)
            self.monitoring_geiger_plot.set_points(points)
            self.radiation_geiger_plot.set_points(points)
            self.radiation_plot_status.setText(f"{len(points)}/300 points")

        self.telemetry_table.set_value("Organic Semiconductor ADC Readings", adc_summary)
        self.telemetry_table.set_value("Temperature Measurements", temp_summary)
        self.telemetry_table.set_value("Subsystem Health Indicators", health)
        self.telemetry_table.set_value("Geiger Valid", str(packet.geiger_valid))
        self.telemetry_table.set_value("Geiger Dose Rate (CPS)", f"{packet.geiger_dose_rate_cps:.9g}")
        self.telemetry_table.set_value("Geiger Total Dose (Sv)", f"{packet.geiger_total_dose_sv:.9g}")
        self.telemetry_table.set_value("Geiger HV Voltage", str(packet.geiger_hv_voltage))
        self.telemetry_table.set_value("Geiger Error Flags", f"0x{packet.geiger_error_flags:04x} ({geiger_error_str})")
        self.telemetry_table.set_value("Packet Timestamp", str(packet.timestamp))
        self.telemetry_table.set_value("Health Code", health)
        self.telemetry_table.set_value("Counter", str(packet.counter))
        self.telemetry_table.set_value("Flags", flags)
        self.telemetry_table.set_value("Temperature Valid Mask", f"0x{packet.temperature_valid_mask:04x}")
        self.telemetry_table.set_value("OS ADC Valid Mask", f"0x{packet.os_adc_valid_mask:04x}")
        self.telemetry_table.set_value("Source", source)
        self.telemetry_table.set_value("Last Seen", "now")

        self.packet_table.set_value("Version", str(packet.version))
        self.packet_table.set_value("Message Type", str(packet.message_type))
        self.packet_table.set_value("Flags", flags)
        self.packet_table.set_value("Payload Length", str(packet.payload_length))
        self.packet_table.set_value("Packet Timestamp", str(packet.timestamp))
        self.packet_table.set_value("Counter", str(packet.counter))
        self.packet_table.set_value("Health Code", health)
        self.packet_table.set_value("Temperature Valid Mask", f"0x{packet.temperature_valid_mask:04x}")
        self.packet_table.set_value("Temperature Sensors", temp_summary)
        self.packet_table.set_value("OS ADC Valid Mask", f"0x{packet.os_adc_valid_mask:04x}")
        self.packet_table.set_value("Organic Semiconductor ADC Readings", adc_summary)
        self.packet_table.set_value("Geiger Valid", str(packet.geiger_valid))
        self.packet_table.set_value("Geiger Error Flags", f"0x{packet.geiger_error_flags:04x}")
        self.packet_table.set_value("Geiger Event ID", str(packet.geiger_event_id))
        self.packet_table.set_value("Geiger Dose CPS", f"{packet.geiger_dose_cps:.17g}")
        self.packet_table.set_value("Geiger Dose Rate CPS", f"{packet.geiger_dose_rate_cps:.9g}")
        self.packet_table.set_value("Geiger Total Dose Sv", f"{packet.geiger_total_dose_sv:.9g}")
        self.packet_table.set_value("Geiger Dose Time Sec", str(packet.geiger_dose_time_sec))
        self.packet_table.set_value("Geiger Stats Time Sec", str(packet.geiger_stats_time_sec))
        self.packet_table.set_value("Geiger HV Voltage", str(packet.geiger_hv_voltage))
        self.packet_table.set_value("Geiger Stat Error %", str(packet.geiger_stat_error_percent))
        self.packet_table.set_value("Geiger Stat Cell Count", str(packet.geiger_stat_cell_count))
        self.packet_table.set_value("TCP Server", tcp_state)

        self.radiation_table.set_value("Geiger Valid", str(packet.geiger_valid))
        self.radiation_table.set_value("Geiger Event ID", str(packet.geiger_event_id))
        self.radiation_table.set_value("Geiger Dose CPS", f"{packet.geiger_dose_cps:.17g}")
        self.radiation_table.set_value("Geiger Dose Rate CPS", f"{packet.geiger_dose_rate_cps:.9g}")
        self.radiation_table.set_value("Geiger Total Dose Sv", f"{packet.geiger_total_dose_sv:.9g}")
        self.radiation_table.set_value("Geiger Dose Time Sec", str(packet.geiger_dose_time_sec))
        self.radiation_table.set_value("Geiger Stats Time Sec", str(packet.geiger_stats_time_sec))
        self.radiation_table.set_value("Geiger HV Voltage", str(packet.geiger_hv_voltage))
        self.radiation_table.set_value("Geiger Stat Error %", str(packet.geiger_stat_error_percent))
        self.radiation_table.set_value("Geiger Stat Cell Count", str(packet.geiger_stat_cell_count))
        self.radiation_table.set_value("Geiger Error Flags", f"0x{packet.geiger_error_flags:04x} ({geiger_error_str})")

        for index, value in enumerate(packet.temperatures):
            validity = "valid" if packet.temperature_valid(index) else "invalid"
            self.temperature_table.set_value(
                f"Temperature sensor {index + 1}", f"{value / 100:.2f} C ({validity})"
            )

        self.health_table.set_value("TCP Server", tcp_state)
        self.health_table.set_value("Flags", flags)
        self.health_table.set_value("Health Code", health)
        self.health_table.set_value("Packets Received", str(self.telemetry_count))
        self.health_table.set_value("Last Telemetry", "now")

        if self.csv_logger is not None and self.csv_logger.active:
            try:
                self.csv_logger.write_packet(packet, source, datetime.now())
            except OSError as exc:
                self.log(f"CSV logging failed: {exc}")
                self.stop_csv_logging()
            else:
                self.csv_log_status.setText(
                    f"CSV: {self.csv_logger.path.name} ({self.csv_logger.packet_count})"
                )

    def format_temperature_summary(self, packet: TelemetryPacket) -> str:
        valid_count = sum(1 for index in range(len(packet.temperatures)) if packet.temperature_valid(index))
        return f"{valid_count}/{len(packet.temperatures)} valid"

    def format_adc_summary(self, packet: TelemetryPacket) -> str:
        valid_count = sum(1 for index in range(len(packet.os_adc_readings)) if packet.os_adc_valid(index))
        return f"{valid_count}/{len(packet.os_adc_readings)} valid"

    def update_telemetry_age(self) -> None:
        if self.last_telemetry_time is None:
            return

        age = time.monotonic() - self.last_telemetry_time
        age_text = f"{age:.1f}s ago"
        self.telemetry_table.set_value("Last Seen", age_text)
        self.health_table.set_value("Last Telemetry", age_text)

        active = age < 2.5
        self.telemetry_state.setText("Telemetry receiving" if active else "Telemetry stale")
        self.telemetry_state.setProperty("active", active)
        self.telemetry_state.style().unpolish(self.telemetry_state)
        self.telemetry_state.style().polish(self.telemetry_state)

    def log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{timestamp}] {message}")

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.csv_logger is not None:
            self.csv_logger.stop()
        self.client.disconnect()
        self.telemetry_receiver.stop()
        self.telemetry_receiver.wait(1000)
        super().closeEvent(event)
