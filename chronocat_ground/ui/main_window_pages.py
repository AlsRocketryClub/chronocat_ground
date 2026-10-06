from __future__ import annotations

import os
import sqlite3
from functools import partial

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
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
from ..plot_widget import HistoryPlotWidget
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
    DEFAULT_TELEMETRY_PORT,
    VALUE_OFF,
    VALUE_ON,
)
from ..telemetry_db import TelemetryDb, archive_database
from .board_map import BoardMapWidget
from .dashboard_page import DashboardPage
from .diagnostics_page import DiagnosticsPage
from .radiation_page import RadiationPage
from .health_page import HealthPage
from .widgets import Panel, SampleCard
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
# Tab order; also the order pages are added to the stack.
VIEWS = (
    VIEW_DASHBOARD,
    VIEW_RADIATION,
    VIEW_SAMPLES,
    VIEW_TEMPERATURE,
    VIEW_HEALTH,
    VIEW_BOARDS,
    VIEW_DIAGNOSTICS,
    VIEW_SETTINGS,
)
_HEALTH_DOT_COLORS = {
    "ok": "#4f9a4f",
    "warning": "#d4a017",
    "error": "#c0392b",
    "unknown": "#b5b5b5",
}


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

        # Host and port live in Settings; the top bar keeps only the actions.
        self.host_input = QLineEdit(DEFAULT_DEVICE_HOST)
        self.port_input = QLineEdit(str(DEFAULT_COMMAND_PORT))

        actions = QVBoxLayout()
        actions.setSpacing(4)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_connection)
        buttons.addWidget(self.connect_button)
        self.csv_log_button = QPushButton("Start CSV logging")
        self.csv_log_button.clicked.connect(self.toggle_csv_logging)
        buttons.addWidget(self.csv_log_button)
        actions.addLayout(buttons)
        notes = QHBoxLayout()
        notes.setSpacing(8)
        self.connection_target_label = QLabel()
        self.connection_target_label.setObjectName("smallNote")
        notes.addWidget(self.connection_target_label)
        notes.addStretch(1)
        self.csv_log_status = QLabel("CSV off")
        self.csv_log_status.setObjectName("smallNote")
        notes.addWidget(self.csv_log_status)
        actions.addLayout(notes)
        title_row.addLayout(actions)
        self.host_input.textChanged.connect(self._update_connection_target)
        self.port_input.textChanged.connect(self._update_connection_target)
        self._update_connection_target()

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
        topbar_layout.addLayout(self.build_tabs())

        return topbar

    def fit_action_buttons(self) -> None:
        """Size each top-bar button for the longer label it toggles to.

        Called once the window is built, so the stylesheet's font and padding apply.
        """
        for button, longest in ((self.connect_button, "Disconnect"), (self.csv_log_button, "Stop CSV logging")):
            label = button.text()
            button.ensurePolished()
            button.setText(longest)
            button.setMinimumWidth(button.sizeHint().width())
            button.setText(label)

    def _update_connection_target(self) -> None:
        target = f"{self.host_input.text().strip()}:{self.port_input.text().strip()}"
        self.connection_target_label.setText(f"Board {target}")

    def build_tabs(self) -> QHBoxLayout:
        tabs = QHBoxLayout()
        tabs.setSpacing(4)
        for view in VIEWS:
            button = QPushButton(view)
            button.setObjectName("navTab")
            button.clicked.connect(lambda _checked=False, selected=view: self.switch_view(selected))
            self.view_buttons[view] = button
            tabs.addWidget(button)
        tabs.addStretch(1)
        self.set_health_dot("unknown")
        return tabs

    def set_health_dot(self, state: str) -> None:
        """Colour the dot on the HEALTH tab so problems show from any page."""
        if getattr(self, "_health_dot_state", None) == state:
            return
        self._health_dot_state = state
        pixmap = QPixmap(10, 10)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(_HEALTH_DOT_COLORS.get(state, _HEALTH_DOT_COLORS["unknown"])))
        painter.drawEllipse(0, 0, 10, 10)
        painter.end()
        self.view_buttons[VIEW_HEALTH].setIcon(QIcon(pixmap))

    def scroll_page(self, page: QWidget) -> QScrollArea:
        scroll = QScrollArea()
        scroll.setObjectName("pageScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setWidget(page)
        return scroll

    def build_dashboard_page(self) -> QWidget:
        self.dashboard = DashboardPage(
            open_health=lambda: self.switch_view(VIEW_HEALTH),
            open_geiger_plot=lambda: self.show_geiger_dialog(),
        )
        return self.dashboard

    def build_radiation_page(self) -> QWidget:
        self.radiation = RadiationPage(
            self.build_geiger_controls_panel(),
            open_flight_history=lambda: self.show_geiger_dialog(),
        )
        return self.radiation

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

    def build_samples_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.sample_cards = [SampleCard(SAMPLE_CHANNELS[slot]) for slot in sorted(SAMPLE_CHANNELS)]
        for card in self.sample_cards:
            card.graph_requested.connect(self.show_adc_graph_dialog)

        # One column per material, channels top to bottom in ADC order.
        columns = QHBoxLayout()
        columns.setSpacing(12)
        for index, (material, channels) in enumerate(SAMPLE_COLUMNS):
            material_panel = Panel()
            # Fixed height so both columns' cards line up despite the unit switch.
            header_widget = QWidget()
            header_widget.setFixedHeight(26)
            header = QHBoxLayout(header_widget)
            header.setContentsMargins(0, 0, 0, 0)
            title = QLabel(material.upper())
            title.setObjectName("panelTitle")
            header.addWidget(title)
            header.addStretch(1)
            if index == len(SAMPLE_COLUMNS) - 1:
                # The unit switch sits in the last header instead of taking a row.
                header.addWidget(self._build_samples_unit_switch())
            material_panel.layout.addWidget(header_widget)
            for channel in channels:
                material_panel.layout.addWidget(self.sample_cards[channel.slot])
            columns.addWidget(material_panel, 1)
        layout.addLayout(columns)

        layout.addStretch(1)
        return page

    def _build_samples_unit_switch(self) -> QWidget:
        switch = QWidget()
        row = QHBoxLayout(switch)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self.samples_unit_buttons = {}
        for mode, text in (("voltage", "Voltage"), ("raw", "Raw24")):
            button = QPushButton(text)
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setChecked(mode == self.samples_display_mode)
            button.clicked.connect(lambda _checked=False, mode=mode: self.set_samples_display_mode(mode))
            row.addWidget(button)
            self.samples_unit_buttons[mode] = button
        return switch

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

        connection_panel = Panel("CONNECTION")
        connection_grid = QGridLayout()
        connection_grid.setHorizontalSpacing(8)
        connection_grid.addWidget(QLabel("Board IP"), 0, 0)
        self.host_input.setMaximumWidth(200)
        connection_grid.addWidget(self.host_input, 0, 1)
        connection_grid.addWidget(QLabel("Command port"), 1, 0)
        self.port_input.setMaximumWidth(200)
        connection_grid.addWidget(self.port_input, 1, 1)
        connection_grid.setColumnStretch(2, 1)
        connection_panel.layout.addLayout(connection_grid)
        connection_note = QLabel(
            f"Commands go to the board over TCP. Telemetry arrives on UDP port "
            f"{DEFAULT_TELEMETRY_PORT}; the firmware sends it to 172.16.18.100, so this "
            "computer must use that address. Changes apply on the next Connect."
        )
        connection_note.setObjectName("smallNote")
        connection_note.setWordWrap(True)
        connection_panel.layout.addWidget(connection_note)
        layout.addWidget(connection_panel)

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
        log_panel = Panel("OPERATOR LOG")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(300)
        self.log_view.setMinimumHeight(220)
        log_panel.layout.addWidget(self.log_view)
        self.diagnostics = DiagnosticsPage(self.build_command_panel(), log_panel)
        return self.diagnostics

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
        self.dashboard.clear()
        self.radiation.clear()
        self.packet_loss.clear()
        for card in self.sample_cards:
            card.plot.set_points([])
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
        self.pages.setCurrentIndex(VIEWS.index(view))
        if view == VIEW_SAMPLES:
            # Sample plots are only refreshed while visible; catch up now.
            self.refresh_sample_cards()
        for name, button in self.view_buttons.items():
            button.setObjectName("navTabActive" if name == view else "navTab")
            button.style().unpolish(button)
            button.style().polish(button)
