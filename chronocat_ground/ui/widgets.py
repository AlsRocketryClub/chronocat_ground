from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..plot_widget import PlotWidget
from ..protocol import AD7177_CHANNEL_COUNT


class Panel(QFrame):
    """The standard bordered container used by application pages."""

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
    """A compact title/value/subtitle metric card."""

    def __init__(self, title: str, subtitle: str = "", value: str = "—") -> None:
        super().__init__()
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("kpiLabel")

        self.value_label = QLabel(value)
        self.value_label.setObjectName("kpiValue")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        if subtitle:
            self.subtitle_label = QLabel(subtitle)
            self.subtitle_label.setObjectName("kpiSub")
            self.subtitle_label.setWordWrap(True)
            layout.addWidget(self.subtitle_label)

    def set_value(self, value: str) -> None:
        self.value_label.setText(value)


class ValueTable(QTableWidget):
    """Read-only two-column table with stable label-based updates."""

    def __init__(self, rows: list[tuple[str, ...]], headers: tuple[str, ...] | None = None) -> None:
        num_cols = len(rows[0]) if rows else 2
        super().__init__(len(rows), num_cols)
        self._value_items: dict[tuple[str, int], QTableWidgetItem] = {}
        self.setObjectName("dataTable")
        self.setShowGrid(True)
        self.setAlternatingRowColors(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.verticalHeader().setVisible(False)
        self.horizontalHeader().setStretchLastSection(True)
        for col in range(num_cols):
            self.horizontalHeader().setSectionResizeMode(col, QHeaderView.Stretch)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        if headers is None:
            self.horizontalHeader().setVisible(False)
        else:
            self.setHorizontalHeaderLabels(headers)

        for row_index, row_data in enumerate(rows):
            for col, value in enumerate(row_data):
                self.setItem(row_index, col, QTableWidgetItem(value))
            label = self.item(row_index, 0)
            if label is not None:
                for col in range(1, num_cols):
                    target = self.item(row_index, col)
                    if target is not None:
                        self._value_items[(label.text(), col)] = target

        self.resizeRowsToContents()

    def set_value(self, name: str, value: str, col: int = 1) -> None:
        target = self._value_items.get((name, col))
        if target is not None and target.text() != value:
            target.setText(value)

    def set_state(self, name: str, state: str, col: int = 1) -> None:
        target = self._value_items.get((name, col))
        if target is None:
            return
        colors = {
            "healthy": "#d8ead8",
            "warning": "#fff0c7",
            "error": "#f5d8d8",
            "unknown": "#eeeeee",
        }
        background = QColor(colors.get(state, colors["unknown"]))
        foreground = QColor("#111111")
        for column in range(self.columnCount()):
            item = self.item(target.row(), column)
            if item is not None:
                item.setBackground(background)
                item.setForeground(foreground)

    def expand_to_contents(self) -> None:
        self.resizeRowsToContents()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        header_height = self.horizontalHeader().height() if self.horizontalHeader().isVisible() else 0
        rows_height = sum(self.rowHeight(row) for row in range(self.rowCount()))
        self.setFixedHeight(header_height + rows_height + 2 * self.frameWidth() + 4)


class HealthSection(QFrame):
    """A prominent subsystem status with independently expandable details."""

    expanded_changed = Signal(bool)

    def __init__(self, title: str, detail: QWidget) -> None:
        super().__init__()
        self.setObjectName("healthSection")
        self._title = title
        self._expanded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(5)

        header = QHBoxLayout()
        title_label = QLabel(title)
        title_label.setObjectName("healthSectionTitle")
        header.addWidget(title_label)
        header.addStretch(1)
        self.toggle_button = QPushButton("Show details")
        self.toggle_button.setObjectName("healthDetailsButton")
        self.toggle_button.clicked.connect(self.toggle_details)
        header.addWidget(self.toggle_button)
        layout.addLayout(header)

        self.status_label = QLabel("WAITING")
        self.status_label.setObjectName("healthSectionStatus")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.detail = detail
        self.detail.setVisible(False)
        layout.addWidget(self.detail)
        self.set_status("WAITING", "unknown")

    def set_status(self, text: str, state: str) -> None:
        self.status_label.setText(text)
        self.setProperty("state", state)
        self.style().unpolish(self)
        self.style().polish(self)

    def toggle_details(self) -> None:
        self._expanded = not self._expanded
        self.detail.setVisible(self._expanded)
        self.toggle_button.setText("Hide details" if self._expanded else "Show details")
        self.expanded_changed.emit(self._expanded)

    @property
    def expanded(self) -> bool:
        return self._expanded


class SampleCard(QFrame):
    """One ADC sample card and its rolling plot."""

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
        self.toggle_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.reading_label = QLabel("Reading: —")
        self.reading_label.setObjectName("sampleMetric")
        self.temperature_label = QLabel("Status: —")
        self.temperature_label.setObjectName("sampleMetric")
        self.meta_label = QLabel(f"ADC{adc_index} CH{channel_index}")
        self.meta_label.setObjectName("smallNote")

        self.plot = PlotWidget(
            "Raw24",
            "No data",
            hover_label="Raw24",
            on_click=lambda: self.graph_requested.emit(self.slot),
            monitor_mode=True,
        )
        self.plot.on_double_click = lambda: self.graph_requested.emit(self.slot)
        self.plot.setMinimumHeight(140)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self.toggle_button)
        layout.addWidget(self.reading_label)
        layout.addWidget(self.temperature_label)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.plot)

    def set_reading(self, reading: str, status: str, valid: bool = True) -> None:
        if valid:
            self.reading_label.setText(f"Reading: {reading}")
            self.temperature_label.setText(f"Status: {status}")
        else:
            self.reading_label.setText(f"Reading: INVALID ({reading})")
            self.temperature_label.setText(f"Status: INVALID/STALE; {status}")

    def set_points(self, points: Sequence[tuple[float, float]]) -> None:
        self.plot.set_points(points)
