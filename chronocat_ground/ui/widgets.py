from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
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
        self.value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

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
