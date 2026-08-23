from __future__ import annotations

import time
from datetime import datetime

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget


class PlotWidget(pg.PlotWidget):
    """Drop-in replacement for LinePlotWidget using pyqtgraph."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(
        self,
        y_label: str,
        empty_text: str = "Waiting for telemetry",
        on_click=None,
        absolute_time: bool = False,
    ) -> None:
        super().__init__()
        self.setObjectName("linePlot")
        self.setMinimumHeight(180)
        self.y_label = y_label
        self.empty_text = empty_text
        self.absolute_time = absolute_time

        if on_click is not None:
            self.clicked.connect(on_click)
            self.setCursor(Qt.PointingHandCursor)

        self._points: list[tuple[float, float, float]] = []
        self._abs_time = absolute_time
        self._on_double_click = None
        self._first_draw = True

        # Plot configuration
        self.setBackground("#ffffff")
        self.showGrid(x=True, y=True, alpha=0.15)
        self.setLabel("left", y_label)
        self.getAxis("left").setWidth(55)
        self.getAxis("bottom").setHeight(35)

        # Style the grid and axes
        pen = pg.mkPen(color="#d0d0d0", width=1)
        self.getAxis("left").setPen(pen)
        self.getAxis("bottom").setPen(pen)

        # Add crosshair
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color="#888888", style=Qt.DashLine))
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color="#888888", style=Qt.DashLine))
        self.addItem(self._vline, ignoreBounds=True)
        self.addItem(self._hline, ignoreBounds=True)
        self._vline.setVisible(False)
        self._hline.setVisible(False)

        # Plot curve
        self._curve = self.plot(pen=pg.mkPen(color="#111111", width=2), symbol=None)

        # Empty label
        self._empty_label = QLabel(empty_text, self)
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: #888888; font-size: 13px;")
        self._empty_label.setVisible(True)

        # Stats overlay (top-right)
        self._stats_label = QLabel(self)
        self._stats_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self._stats_label.setStyleSheet(
            "color: #333333; font-size: 11px; font-family: SF Mono, Menlo, Consolas, monospace; "
            "background: rgba(255,255,255,180); padding: 6px 4px;"
        )
        self._stats_label.setVisible(False)

        # Tooltip label
        self._tooltip_label = QLabel(self)
        self._tooltip_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self._tooltip_label.setStyleSheet(
            "color: #111111; font-size: 11px; background: rgba(255,255,255,220); "
            "padding: 4px; border: 1px solid #cccccc;"
        )
        self._tooltip_label.setVisible(False)

        # Time window controls
        self._time_window = None  # None = auto / show all
        self._time_window_s = None

        # Clamp x-axis so latest point (0) is always at right edge
        if not absolute_time:
            self.plotItem.vb.setLimits(xMax=0)

    @property
    def on_double_click(self):
        return self._on_double_click

    @on_double_click.setter
    def on_double_click(self, callback):
        self._on_double_click = callback

    def set_points(self, points: list[tuple]) -> None:
        if not points:
            self._points = []
            self._update_empty()
            return

        if len(points[0]) == 3:
            raw = [(mono, wall, val) for mono, wall, val in points]
        else:
            now = time.time()
            raw = [(x, now, y) for x, y in points]

        if self._abs_time:
            self._points = raw
        else:
            max_mono = max(mono for mono, _wall, _val in raw)
            self._points = [((mono - max_mono), wall, val) for mono, wall, val in raw]

        self._update_empty()
        self._redraw()

    def _update_empty(self) -> None:
        visible = len(self._points) < 2
        self._empty_label.setVisible(visible)
        self._stats_label.setVisible(not visible and len(self._points) >= 2)

    def _redraw(self) -> None:
        if len(self._points) < 2:
            self._curve.setData([], [])
            return

        x = np.array([p[0] for p in self._points])
        y = np.array([p[2] for p in self._points])

        if self._time_window_s is not None and not self._abs_time:
            max_x = x.max()
            mask = x >= max_x - self._time_window_s
            x = x[mask]
            y = y[mask]

        self._curve.setData(x, y)

        if self._first_draw:
            self._first_draw = False
            if not self._abs_time:
                self.enableAutoRange(x=False, y=True)
                self.setXRange(x.min(), 0, padding=0)
            else:
                self.enableAutoRange(x=True, y=True)

        self._update_stats()

    def _update_stats(self) -> None:
        if len(self._points) < 2:
            return

        y = np.array([p[2] for p in self._points])
        stats = f"n={len(y)}  min={y.min():.4g}  max={y.max():.4g}  avg={y.mean():.4g}"
        self._stats_label.setText(stats)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event is None:
            return
        w, h = event.size().width(), event.size().height()
        self._empty_label.setGeometry(0, 0, w, h)
        self._stats_label.setGeometry(w - 220, 4, 216, 26)
        self._tooltip_label.setGeometry(4, 4, 200, 40)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.double_clicked.emit()
            if self._on_double_click is not None:
                self._on_double_click()
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        super().mouseMoveEvent(event)

        if len(self._points) < 2:
            self._tooltip_label.setVisible(False)
            return

        # Show crosshair
        mouse_point = self.plotItem.vb.mapSceneToView(event.position())
        self._vline.setVisible(True)
        self._hline.setVisible(True)
        self._vline.setPos(mouse_point.x())
        self._hline.setPos(mouse_point.y())

        # Find nearest point and show tooltip
        x_arr = np.array([p[0] for p in self._points])
        idx = int(np.argmin(np.abs(x_arr - mouse_point.x())))
        mono, wall, val = self._points[idx]

        if self._abs_time:
            ts = datetime.fromtimestamp(wall).strftime("%H:%M:%S")
        else:
            max_mono = max(p[0] for p in self._points)
            elapsed = mono - max_mono
            ts = "now" if abs(elapsed) < 0.01 else f"{elapsed:.1f}s ago"

        self._tooltip_label.setText(f"{ts}  {self.y_label}: {val:.6g}")
        self._tooltip_label.setVisible(True)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self._tooltip_label.setVisible(False)
