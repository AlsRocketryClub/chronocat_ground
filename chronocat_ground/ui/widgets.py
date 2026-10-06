from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QColor
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
from ..sample_layout import SampleChannel


# Gap between panels and around the window; small so content gets the space.
PAGE_SPACING = 6


class Panel(QFrame):
    """The standard bordered container used by application pages."""

    def __init__(self, title: str | None = None) -> None:
        super().__init__()
        self.setObjectName("panel")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.setSpacing(8)

        if title is not None:
            title_label = QLabel(title)
            title_label.setObjectName("panelTitle")
            self.layout.addWidget(title_label)


class ValueTable(QTableWidget):
    """Read-only two-column table with stable label-based updates."""

    def __init__(self, rows: list[tuple[str, ...]], headers: tuple[str, ...] | None = None) -> None:
        num_cols = len(rows[0]) if rows else 2
        super().__init__(len(rows), num_cols)
        self._value_items: dict[tuple[str, int], QTableWidgetItem] = {}
        self._expand_height_to_contents = False
        self._fit_height_pending = False
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
            self._schedule_content_height_update()

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
        self._expand_height_to_contents = True
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._update_content_height()

    def _schedule_content_height_update(self) -> None:
        if not self._expand_height_to_contents or self._fit_height_pending:
            return
        self._fit_height_pending = True
        QTimer.singleShot(0, self._update_content_height)

    def _update_content_height(self) -> None:
        self._fit_height_pending = False
        if not self._expand_height_to_contents:
            return
        self.resizeRowsToContents()
        header_height = self.horizontalHeader().height() if self.horizontalHeader().isVisible() else 0
        rows_height = sum(self.rowHeight(row) for row in range(self.rowCount()))
        scrollbar_height = (
            self.horizontalScrollBar().sizeHint().height()
            if self.horizontalScrollBar().isVisible()
            else 0
        )
        target_height = (
            header_height + rows_height + scrollbar_height + 2 * self.frameWidth() + 12
        )
        if self.height() != target_height:
            self.setFixedHeight(target_height)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._schedule_content_height_update()

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._schedule_content_height_update()


class SampleCard(QFrame):
    """One ADC sample channel, the temperatures of its two samples, and a rolling plot."""

    graph_requested = Signal(int)

    def __init__(self, channel: SampleChannel) -> None:
        super().__init__()
        self.setObjectName("sampleCard")
        self.slot = channel.slot

        self.toggle_button = QPushButton(channel.name)
        self.toggle_button.setObjectName("sampleToggle")
        self.toggle_button.clicked.connect(lambda: self.graph_requested.emit(self.slot))
        self.toggle_button.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)

        self.reading_label = QLabel("Reading: —")
        self.reading_label.setObjectName("sampleMetric")
        self.status_label = QLabel("Status: —")
        self.status_label.setObjectName("sampleMetric")
        self.temperatures_label = QLabel("Temperatures: —")
        self.temperatures_label.setObjectName("sampleMetric")
        self.meta_label = QLabel(channel.location)
        self.meta_label.setObjectName("smallNote")

        self.plot = PlotWidget(
            "Volts (V)",
            "No data",
            hover_label="Volts",
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
        layout.addWidget(self.status_label)
        layout.addWidget(self.temperatures_label)
        layout.addWidget(self.meta_label)
        layout.addWidget(self.plot)

    def set_reading(self, reading: str, status: str, valid: bool = True) -> None:
        if valid:
            self.reading_label.setText(f"Reading: {reading}")
            self.status_label.setText(f"Status: {status}")
        else:
            self.reading_label.setText(f"Reading: INVALID ({reading})")
            self.status_label.setText(f"Status: INVALID/STALE; {status}")

    def set_temperatures(self, temperatures: Sequence[tuple[str, float | None]]) -> None:
        """Show each sample's sensor as `label value`, or `label —` when invalid."""
        parts = [
            f"{label} {value:.2f} °C" if value is not None else f"{label} —"
            for label, value in temperatures
        ]
        self.temperatures_label.setText("Temperatures: " + "  ·  ".join(parts))

    def set_points(self, points: Sequence[tuple[float, float]]) -> None:
        self.plot.set_points(points)

    def set_value_axis(self, y_label: str, hover_label: str) -> None:
        self.plot.set_y_label(y_label, hover_label)
