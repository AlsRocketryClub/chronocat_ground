from __future__ import annotations

from collections import deque
from datetime import datetime
import os
import threading
import time

from PySide6.QtCore import QObject, QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
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
    AD7177_CHANNEL_COUNT,
    TELEMETRY_OS_ADC_COUNT,
    COMMAND_PING,
    COMMAND_GEIGER_CLEAR_HISTORY,
    COMMAND_GEIGER_RESET_ACCUMULATED_DOSE,
    COMMAND_GEIGER_RESET_STATS,
    COMMAND_TELEMETRY_SET,
    COMMAND_TELEMETRY_STATUS,
    DEFAULT_COMMAND_PORT,
    DEFAULT_DEVICE_HOST,
    DEFAULT_TELEMETRY_PORT,
    VALUE_OFF,
    VALUE_ON,
    CommandResponse,
    GeigerReading,
    TelemetryPacket,
    ad7177_status_names,
    command_name,
    geiger_reset_actions_name,
    geiger_error_names,
    status_name,
    telemetry_health_name,
    telemetry_value_name,
    tcp_status_name,
)
from .telemetry_csv import CSV_MODE_FULL, CSV_MODE_GEIGER_ONLY, TelemetryCsvLogger
from .telemetry_db import TelemetryDb
from .telemetry_receiver import TelemetryReceiver


VIEW_MONITORING = "MONITORING"
VIEW_RADIATION = "RADIATION"
VIEW_SAMPLES = "SAMPLES"
VIEW_TEMPERATURE = "TEMPERATURE MEASUREMENTS"
VIEW_HEALTH = "SUBSYSTEM HEALTH"
VIEW_SETTINGS = "SETTINGS"


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
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
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
    def __init__(self, y_label: str, empty_text: str = "Waiting for telemetry", on_click=None) -> None:
        super().__init__()
        self.setObjectName("linePlot")
        self.setMinimumHeight(180)
        self.points: list[tuple[float, float]] = []
        self.y_label = y_label
        self.empty_text = empty_text
        self.on_click = on_click
        if on_click is not None:
            self.setCursor(Qt.PointingHandCursor)

    def set_points(self, points: list[tuple[float, float]]) -> None:
        if not points:
            self.points = []
            self.update()
            return
        max_x = max(x for x, _y in points)
        self.points = [((x - max_x) / 1000.0, y) for x, y in points]
        self.update()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if self.on_click is not None and event.button() == Qt.LeftButton:
            self.on_click()
            return
        super().mousePressEvent(event)

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
        painter.drawText(QRectF(8, bounds.height() - 26, bounds.width() - 16, 22), Qt.AlignRight | Qt.AlignVCenter, "seconds ago")

        if len(self.points) < 2:
            painter.drawText(plot, Qt.AlignCenter, self.empty_text)
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

        painter.save()
        font = painter.font()
        if font.pointSize() > 1:
            font.setPointSize(font.pointSize() - 1)
        painter.setFont(font)
        for index in range(1, 5):
            fx = plot.left() + plot.width() * index / 5
            value = min_x + (max_x - min_x) * index / 5
            label = "now" if abs(value) < 0.01 else f"{value:.0f}s" if abs(value) >= 10 else f"{value:.1f}s"
            text_rect = QRectF(fx - 20, plot.bottom() + 2, 40, 20)
            painter.drawText(text_rect, Qt.AlignCenter, label)
        painter.restore()

        polyline = QPolygonF()
        vp = event.rect()
        vis_left = min_x + ((vp.left() - plot.left()) / plot.width()) * (max_x - min_x)
        vis_right = min_x + ((vp.right() - plot.left()) / plot.width()) * (max_x - min_x)
        for x_value, y_value in self.points:
            if x_value < vis_left:
                continue
            if x_value > vis_right:
                break
            x = plot.left() + ((x_value - min_x) / (max_x - min_x)) * plot.width()
            y = plot.bottom() - ((y_value - min_y) / (max_y - min_y)) * plot.height()
            polyline.append(QPointF(x, y))

        painter.setPen(QPen(QColor("#111111"), 2))
        painter.drawPolyline(polyline)


class SampleCard(QFrame):
    graph_requested = Signal(int)

    def __init__(self, device_name: str, slot: int) -> None:
        super().__init__()
        self.setObjectName("sampleCard")
        self.slot = slot
        adc_index = slot // AD7177_CHANNEL_COUNT
        channel_index = slot % AD7177_CHANNEL_COUNT

        self.toggle_button = QPushButton(device_name)
        self.toggle_button.setObjectName("sampleToggle")
        self.toggle_button.clicked.connect(lambda: self.graph_requested.emit(self.slot))

        self.reading_label = QLabel("Newest reading: X")
        self.reading_label.setObjectName("sampleMetric")
        self.temperature_label = QLabel("Status: X")
        self.temperature_label.setObjectName("sampleMetric")
        self.meta_label = QLabel(f"ADC{adc_index} CH{channel_index}")
        self.meta_label.setObjectName("smallNote")

        self.plot = LinePlotWidget("raw24", "Waiting for ADC telemetry", on_click=lambda: self.graph_requested.emit(self.slot))
        self.plot.setMinimumHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.reading_label)
        layout.addWidget(self.temperature_label)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.plot)

    def set_reading(self, reading: str, status: str) -> None:
        self.reading_label.setText(f"Newest reading: {reading}")
        self.temperature_label.setText(f"Status: {status}")

    def set_points(self, points: list[tuple[float, float]]) -> None:
        self.plot.set_points(points)


class CommandSignals(QObject):
    completed = Signal(object)
    failed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("chronocat_ground")
        self.resize(1180, 760)
        self.setMinimumSize(760, 520)

        self.client = CommandClient()
        self.command_in_progress = False
        self.command_signals = CommandSignals(self)
        self.command_signals.completed.connect(self.command_completed)
        self.command_signals.failed.connect(self.command_failed)
        self.telemetry_count = 0
        self.last_telemetry_time: float | None = None
        self.view_buttons: dict[str, QPushButton] = {}
        self.geiger_dose_rate_histories: list[deque[tuple[float, float]]] = [
            deque(maxlen=300), deque(maxlen=300)
        ]
        self.adc_raw24_histories: list[deque[tuple[float, float]]] = [
            deque(maxlen=300) for _ in range(TELEMETRY_OS_ADC_COUNT)
        ]
        self.adc_db = TelemetryDb("chronocat_adc.db")
        self.sample_cards: list[SampleCard] = []
        self.adc_dialog: QDialog | None = None
        self.adc_dialog_slot: int | None = None
        self.adc_dialog_plot: LinePlotWidget | None = None
        self.adc_dialog_latest: QLabel | None = None
        self.adc_dialog_scroll: QScrollArea | None = None
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
        self.pages.addWidget(self.scroll_page(self.build_settings_page()))
        body.addWidget(self.pages, 1)

        return root

    def build_topbar(self) -> QFrame:
        topbar = QFrame()
        topbar.setObjectName("topbar")
        topbar_layout = QHBoxLayout(topbar)
        topbar_layout.setContentsMargins(12, 12, 12, 12)
        topbar_layout.setSpacing(16)

        script_dir = os.path.dirname(__file__)
        logo_path = os.path.join(script_dir, "CHRONO-CAT_logo.png")
        logo_pixmap = QPixmap(logo_path)
        if not logo_pixmap.isNull():
            logo_pixmap = logo_pixmap.scaledToHeight(40, Qt.SmoothTransformation)
            logo_label = QLabel()
            logo_label.setPixmap(logo_pixmap)
            logo_label.setFixedSize(logo_pixmap.size())
            topbar_layout.addWidget(logo_label)

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
        self.csv_mode_combo = QComboBox()
        self.csv_mode_combo.addItem("Full telemetry", CSV_MODE_FULL)
        self.csv_mode_combo.addItem("Geiger only", CSV_MODE_GEIGER_ONLY)
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
        connection_bar.addWidget(self.csv_mode_combo)
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

        for view in (VIEW_MONITORING, VIEW_RADIATION, VIEW_SAMPLES, VIEW_TEMPERATURE, VIEW_HEALTH, VIEW_SETTINGS):
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
        self.tick_card = StatCard("PACKET TIMESTAMP", "firmware tick count, ms")
        self.tcp_card = StatCard("HEALTH CODE", "TCP server state")
        self.count_card = StatCard("TELEMETRY STORAGE", "packets received this session")
        self.geiger_dose_rate_card = StatCard("GEIGER 1 DOSE RATE", "dose rate, CPS")
        self.geiger_total_dose_card = StatCard("GEIGER 1 TOTAL DOSE", "accumulated dose, Sv")
        self.geiger_hv_card = StatCard("GEIGER 1 HV", "HV voltage")
        self.geiger_errors_card = StatCard("GEIGER 1 ERRORS", "error flags")
        self.geiger_2_dose_rate_card = StatCard("GEIGER 2 DOSE RATE", "dose rate, CPS")
        self.geiger_2_total_dose_card = StatCard("GEIGER 2 TOTAL DOSE", "accumulated dose, Sv")
        self.geiger_2_hv_card = StatCard("GEIGER 2 HV", "HV voltage")
        self.geiger_2_errors_card = StatCard("GEIGER 2 ERRORS", "error flags")

        cards = QGridLayout()
        cards.setSpacing(8)
        cards.addWidget(self.seq_card, 0, 0)
        cards.addWidget(self.tick_card, 0, 1)
        cards.addWidget(self.tcp_card, 0, 2)
        cards.addWidget(self.count_card, 0, 3)
        cards.addWidget(self.geiger_dose_rate_card, 1, 0)
        cards.addWidget(self.geiger_total_dose_card, 1, 1)
        cards.addWidget(self.geiger_hv_card, 1, 2)
        cards.addWidget(self.geiger_errors_card, 1, 3)
        cards.addWidget(self.geiger_2_dose_rate_card, 2, 0)
        cards.addWidget(self.geiger_2_total_dose_card, 2, 1)
        cards.addWidget(self.geiger_2_hv_card, 2, 2)
        cards.addWidget(self.geiger_2_errors_card, 2, 3)
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
        chart_title = QLabel("GEIGER 1 DOSE RATE TIME-SERIES")
        chart_title.setObjectName("panelTitle")
        self.timestamp_label = QLabel("RECEPTION TIMESTAMP: X")
        self.timestamp_label.setObjectName("smallNote")
        chart_header.addWidget(chart_title)
        chart_header.addStretch(1)
        chart_header.addWidget(self.timestamp_label)
        chart_panel.layout.addLayout(chart_header)
        self.monitoring_geiger_plot = LinePlotWidget("dose rate CPS", "Waiting for Geiger telemetry")
        chart_panel.layout.addWidget(self.monitoring_geiger_plot)
        geiger_2_title = QLabel("GEIGER 2 DOSE RATE TIME-SERIES")
        geiger_2_title.setObjectName("panelTitle")
        chart_panel.layout.addWidget(geiger_2_title)
        self.monitoring_geiger_2_plot = LinePlotWidget("dose rate CPS", "Waiting for Geiger 2 telemetry")
        chart_panel.layout.addWidget(self.monitoring_geiger_2_plot)
        return chart_panel

    def build_radiation_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        cards = QGridLayout()
        cards.setSpacing(8)
        self.radiation_dose_rate_card = StatCard("GEIGER 1 DOSE RATE", "dose rate, CPS")
        self.radiation_total_dose_card = StatCard("GEIGER 1 TOTAL DOSE", "accumulated dose, Sv")
        self.radiation_hv_card = StatCard("GEIGER 1 HV", "HV voltage")
        self.radiation_errors_card = StatCard("GEIGER 1 ERRORS", "error flags")
        self.radiation_2_dose_rate_card = StatCard("GEIGER 2 DOSE RATE", "dose rate, CPS")
        self.radiation_2_total_dose_card = StatCard("GEIGER 2 TOTAL DOSE", "accumulated dose, Sv")
        self.radiation_2_hv_card = StatCard("GEIGER 2 HV", "HV voltage")
        self.radiation_2_errors_card = StatCard("GEIGER 2 ERRORS", "error flags")
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

        plot_panel = Panel()
        plot_header = QHBoxLayout()
        title = QLabel("GEIGER 1 DOSE RATE TIME-SERIES")
        title.setObjectName("panelTitle")
        self.radiation_plot_status = QLabel("300 point rolling window")
        self.radiation_plot_status.setObjectName("smallNote")
        plot_header.addWidget(title)
        plot_header.addStretch(1)
        plot_header.addWidget(self.radiation_plot_status)
        plot_panel.layout.addLayout(plot_header)
        self.radiation_geiger_plot = LinePlotWidget("dose rate CPS", "Waiting for Geiger telemetry")
        self.radiation_geiger_plot.setMinimumHeight(320)
        plot_panel.layout.addWidget(self.radiation_geiger_plot)
        geiger_2_header = QHBoxLayout()
        geiger_2_title = QLabel("GEIGER 2 DOSE RATE TIME-SERIES")
        geiger_2_title.setObjectName("panelTitle")
        self.radiation_2_plot_status = QLabel("300 point rolling window")
        self.radiation_2_plot_status.setObjectName("smallNote")
        geiger_2_header.addWidget(geiger_2_title)
        geiger_2_header.addStretch(1)
        geiger_2_header.addWidget(self.radiation_2_plot_status)
        plot_panel.layout.addLayout(geiger_2_header)
        self.radiation_geiger_2_plot = LinePlotWidget("dose rate CPS", "Waiting for Geiger 2 telemetry")
        self.radiation_geiger_2_plot.setMinimumHeight(320)
        plot_panel.layout.addWidget(self.radiation_geiger_2_plot)
        layout.addWidget(plot_panel)

        geiger_detail_rows = [
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
        ]
        self.radiation_table = ValueTable(geiger_detail_rows, ("Metric", "Value"))
        table_panel = Panel("GEIGER 1 PACKET DETAILS")
        table_panel.layout.addWidget(self.radiation_table)
        layout.addWidget(table_panel)
        self.radiation_2_table = ValueTable(geiger_detail_rows, ("Metric", "Value"))
        table_2_panel = Panel("GEIGER 2 PACKET DETAILS")
        table_2_panel.layout.addWidget(self.radiation_2_table)
        layout.addWidget(table_2_panel)
        layout.addStretch(1)
        return page

    def build_geiger_controls_panel(self) -> Panel:
        controls_panel = Panel("GEIGER 1 DETECTOR CONTROLS")
        note = QLabel(
            "These commands write detector flash and can take a few seconds before telemetry updates."
        )
        note.setObjectName("smallNote")
        note.setWordWrap(True)
        controls_panel.layout.addWidget(note)

        self.geiger_reset_dose_button = QPushButton("Reset Accumulated Dose")
        self.geiger_clear_history_button = QPushButton("Clear History")
        self.geiger_reset_stats_button = QPushButton("Reset Statistics")

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
        return controls_panel

    def build_samples_summary_panel(self) -> Panel:
        samples_panel = Panel()
        header = QHBoxLayout()
        title = QLabel("SAMPLES")
        title.setObjectName("panelTitle")
        note = QLabel("12 ADC telemetry slots")
        note.setObjectName("smallNote")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(note)
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
        samples_panel.layout.addWidget(self.samples_summary_table)
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
                ("AD7177 Readings", "X"),
                ("Temperature Measurements", "X"),
                ("Subsystem Health Indicators", "X"),
                ("Geiger 1 Valid", "X"),
                ("Geiger 1 Dose Rate (CPS)", "X"),
                ("Geiger 1 Total Dose (Sv)", "X"),
                ("Geiger 1 HV Voltage", "X"),
                ("Geiger 1 Error Flags", "X"),
                ("Geiger 2 Valid", "X"),
                ("Geiger 2 Dose Rate (CPS)", "X"),
                ("Geiger 2 Total Dose (Sv)", "X"),
                ("Geiger 2 HV Voltage", "X"),
                ("Geiger 2 Error Flags", "X"),
                ("Packet Timestamp (ms)", "X"),
                ("Health Code", "X"),
                ("Counter", "X"),
                ("Flags", "X"),
                ("Temperature Valid Mask", "X"),
                ("ADC Legacy Valid Mask", "X"),
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
            "Uplink actions require a command server connection. Geiger memory controls are on the Radiation page."
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
                ("Packet Timestamp (ms)", "X"),
                ("Counter", "X"),
                ("Health Code", "X"),
                ("Temperature Valid Mask", "X"),
                ("Temperature Sensors", "X"),
                ("ADC Legacy Valid Mask", "X"),
                ("AD7177 Readings", "X"),
                ("Geiger 1 Valid", "X"),
                ("Geiger 1 Error Flags", "X"),
                ("Geiger 1 Event ID", "X"),
                ("Geiger 1 Dose CPS", "X"),
                ("Geiger 1 Dose Rate CPS", "X"),
                ("Geiger 1 Total Dose Sv", "X"),
                ("Geiger 1 Dose Time Sec", "X"),
                ("Geiger 1 Stats Time Sec", "X"),
                ("Geiger 1 HV Voltage", "X"),
                ("Geiger 1 Stat Error %", "X"),
                ("Geiger 1 Stat Cell Count", "X"),
                ("Geiger 2 Valid", "X"),
                ("Geiger 2 Error Flags", "X"),
                ("Geiger 2 Event ID", "X"),
                ("Geiger 2 Dose CPS", "X"),
                ("Geiger 2 Dose Rate CPS", "X"),
                ("Geiger 2 Total Dose Sv", "X"),
                ("Geiger 2 Dose Time Sec", "X"),
                ("Geiger 2 Stats Time Sec", "X"),
                ("Geiger 2 HV Voltage", "X"),
                ("Geiger 2 Stat Error %", "X"),
                ("Geiger 2 Stat Cell Count", "X"),
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

            if self.adc_dialog is not None:
                self.adc_dialog.close()

            card = self.sample_cards[slot]
            adc_index = slot // AD7177_CHANNEL_COUNT
            channel_index = slot % AD7177_CHANNEL_COUNT
            title = f"{card.toggle_button.text()} / ADC{adc_index} CH{channel_index}"

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

            latest_label = QLabel(f"{card.reading_label.text()} / {card.temperature_label.text()}")
            latest_label.setObjectName("smallNote")
            latest_label.setWordWrap(True)

            plot = LinePlotWidget("raw24", "Waiting for ADC telemetry")
            plot.setMinimumHeight(400)

            scroll = QScrollArea()
            scroll.setWidgetResizable(False)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(plot)

            frame_layout.addLayout(top_row)
            frame_layout.addWidget(latest_label)
            frame_layout.addWidget(scroll, 1)

            self.adc_dialog = dialog
            self.adc_dialog_slot = slot
            self.adc_dialog_scroll = scroll
            self.adc_dialog_plot = plot
            self.adc_dialog_latest = latest_label

            dialog.finished.connect(lambda _result, opened=dialog: self.clear_adc_graph_dialog(opened))

            points = self._load_dialog_data(slot)
            plot.set_points(points)
            if points:
                plot.setFixedWidth(max(len(points), 876))
            dialog.show()
        except Exception as exc:
            self.log(f"Failed to open ADC graph: {exc}")

    def _load_dialog_data(self, slot: int) -> list[tuple[float, float]]:
        rows = self.adc_db.query(slot, 0)
        if rows:
            return [(float(ts), float(v)) for ts, v in rows]
        if self.adc_raw24_histories[slot]:
            return list(self.adc_raw24_histories[slot])
        return []

    def clear_adc_graph_dialog(self, dialog: QDialog) -> None:
        if self.adc_dialog is not dialog:
            return
        self.adc_dialog = None
        self.adc_dialog_slot = None
        self.adc_dialog_scroll = None
        self.adc_dialog_plot = None
        self.adc_dialog_latest = None

    def update_adc_graph_dialog(self) -> None:
        slot = self.adc_dialog_slot
        if slot is None or self.adc_dialog_plot is None or self.adc_dialog_latest is None or self.adc_dialog_scroll is None:
            return
        if not 0 <= slot < len(self.sample_cards):
            return
        card = self.sample_cards[slot]
        self.adc_dialog_latest.setText(f"{card.reading_label.text()} / {card.temperature_label.text()}")
        points = self._load_dialog_data(slot)
        self.adc_dialog_plot.set_points(points)
        sb = self.adc_dialog_scroll.horizontalScrollBar()
        was_at_end = sb.value() == sb.maximum()
        if points:
            vp_w = self.adc_dialog_scroll.viewport().width()
            w = max(len(points), vp_w)
            if self.adc_dialog_plot.width() != w:
                self.adc_dialog_plot.setFixedWidth(w)
        if was_at_end:
            sb.setValue(sb.maximum())

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

    def build_settings_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        panel = Panel("SETTINGS")
        layout.addWidget(panel)

        db_panel = Panel("ADC DATABASE")
        db_path = "chronocat_adc.db"
        db_size = os.path.getsize(db_path) if os.path.exists(db_path) else 0
        db_count = 0
        try:
            if self.adc_db.conn:
                cur = self.adc_db.conn.execute("SELECT COUNT(*) FROM adc")
                db_count = cur.fetchone()[0]
        except Exception:
            pass
        size_str = f"{db_size:,} bytes" if db_size > 0 else "N/A"
        info = QLabel(
            f"File: {db_path}\nRecords: {db_count:,}\nSize: {size_str}"
        )
        info.setObjectName("smallNote")
        info.setWordWrap(True)
        db_panel.layout.addWidget(info)

        clear_btn = QPushButton("CLEAR DATABASE")
        clear_btn.clicked.connect(self._on_clear_db)
        db_panel.layout.addWidget(clear_btn)

        layout.addWidget(db_panel)
        layout.addStretch(1)
        return page

    def _on_clear_db(self) -> None:
        ret = QMessageBox.question(
            self, "Clear Database",
            "Delete all stored ADC records? In-memory graphs keep current session data.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if ret == QMessageBox.Yes:
            self.adc_db.clear()
            self.log("ADC database cleared")

    def switch_view(self, view: str) -> None:
        order = [VIEW_MONITORING, VIEW_RADIATION, VIEW_SAMPLES, VIEW_TEMPERATURE, VIEW_HEALTH, VIEW_SETTINGS]
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
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            #kpiValue {
                font-size: 18px;
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
            QComboBox {
                background: #ffffff;
                border: 1px solid #777777;
                border-radius: 0px;
                padding: 6px 8px;
                min-width: 80px;
            }
            QComboBox:hover {
                border-color: #555555;
            }
            QComboBox QAbstractItemView {
                background: #ffffff;
                border: 1px solid #777777;
                selection-background-color: #d9d9d9;
                selection-color: #111111;
                outline: none;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
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

        csv_mode = self.csv_mode_combo.currentData()
        self.csv_logger = TelemetryCsvLogger(mode=csv_mode)
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
        self.connection_label.setText("Connected" if connected else "Disconnected")
        self.connection_label.setProperty("connected", connected)
        self.connection_label.style().unpolish(self.connection_label)
        self.connection_label.style().polish(self.connection_label)
        self.connect_button.setText("Disconnect" if connected else "Connect")
        self.connect_button.setEnabled(not self.command_in_progress)
        self.host_input.setEnabled(not self.command_in_progress)
        self.port_input.setEnabled(not self.command_in_progress)

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
            button.setEnabled(connected and not self.command_in_progress)

    def send_command(
        self,
        command: int,
        value: int,
        arg2: int = 0,
        timeout: float | None = None,
    ) -> None:
        if self.command_in_progress:
            self.log("Command already in progress")
            return

        self.command_in_progress = True
        self.update_connection_state()

        def request() -> None:
            try:
                response = self.client.send_command(command, value, arg2, timeout=timeout)
            except (OSError, ValueError, ConnectionError) as exc:
                self.command_signals.failed.emit(str(exc))
            else:
                self.command_signals.completed.emit(response)

        threading.Thread(target=request, daemon=True).start()

    def command_completed(self, response: CommandResponse) -> None:
        self.command_in_progress = False
        self.log_response(response)
        self.update_connection_state()

    def command_failed(self, message: str) -> None:
        self.command_in_progress = False
        self.log(f"Command failed: {message}")
        self.client.disconnect()
        self.update_connection_state()

    def send_geiger_command(self, command: int, action: str, timeout: float | None = None) -> None:
        self.log(f"Sending Geiger command: {action}")
        self.send_command(command, 0, 0, timeout=timeout)

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
        geiger_1 = packet.geiger_reading(0)
        geiger_2 = packet.geiger_reading(1)
        self.update_geiger_cards(
            geiger_1,
            self.geiger_dose_rate_card,
            self.geiger_total_dose_card,
            self.geiger_hv_card,
            self.geiger_errors_card,
        )
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
        self.update_geiger_cards(
            geiger_2,
            self.radiation_2_dose_rate_card,
            self.radiation_2_total_dose_card,
            self.radiation_2_hv_card,
            self.radiation_2_errors_card,
        )
        self.timestamp_label.setText(f"RECEPTION TIMESTAMP: {datetime.now().strftime('%H:%M:%S')}")

        for counter_id, reading in enumerate((geiger_1, geiger_2)):
            if reading is None or not reading.valid:
                continue
            self.geiger_dose_rate_histories[counter_id].append(
                (float(packet.timestamp), reading.dose_rate_cps)
            )
            points = list(self.geiger_dose_rate_histories[counter_id])
            if counter_id == 0:
                self.monitoring_geiger_plot.set_points(points)
                self.radiation_geiger_plot.set_points(points)
                self.radiation_plot_status.setText(f"{len(points)}/300 points")
            else:
                self.monitoring_geiger_2_plot.set_points(points)
                self.radiation_geiger_2_plot.set_points(points)
                self.radiation_2_plot_status.setText(f"{len(points)}/300 points")

        materials = ["TIPs-pentacene", "diF-TES-ADT", "Rubrene"]
        device_types = ["Device 1a", "Device 2a", "Device 1b", "Device 2b"]
        readings_pairs: list[tuple[int, int]] = []
        for reading in packet.ad7177_readings:
            readings_pairs.append((reading.slot, reading.raw24))
            self.adc_raw24_histories[reading.slot].append((float(packet.timestamp), float(reading.raw24)))
            if reading.slot < len(self.sample_cards):
                points = list(self.adc_raw24_histories[reading.slot])
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
        self.adc_db.insert_many(packet.timestamp, readings_pairs)
        self.update_adc_graph_dialog()

        self.telemetry_table.set_value("AD7177 Readings", adc_summary)
        self.telemetry_table.set_value("Temperature Measurements", temp_summary)
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
        self.update_packet_geiger_fields(2, geiger_2)
        self.packet_table.set_value("TCP Server", tcp_state)

        self.update_geiger_detail_table(self.radiation_table, geiger_1)
        self.update_geiger_detail_table(self.radiation_2_table, geiger_2)

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
                    f"CSV ({self.csv_mode_combo.currentText()}): "
                    f"{self.csv_logger.path.name} ({self.csv_logger.packet_count})"
                )

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
        if self.adc_dialog is not None:
            self.adc_dialog.close()
        self.adc_db.close()
        if self.csv_logger is not None:
            self.csv_logger.stop()
        self.client.disconnect()
        self.telemetry_receiver.stop()
        self.telemetry_receiver.wait(1000)
        super().closeEvent(event)
