from __future__ import annotations

import time
from datetime import datetime
from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget


class WallClockAxis(pg.AxisItem):
    """X-axis that formats absolute points as clocks or relative points as seconds."""

    def __init__(self, *args, wall_ref: float = 0.0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._wall_ref = wall_ref
        self._relative = False

    def setWallRef(self, wall_ref: float) -> None:  # noqa: N802
        self._wall_ref = wall_ref

    def setRelative(self, relative: bool) -> None:  # noqa: N802
        self._relative = relative

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        strings = []
        seen: set[str] = set()
        for v in values:
            if self._relative:
                label = "now" if abs(v) < 0.5 else f"{v:.0f} s"
            else:
                wall = self._wall_ref + v
                label = datetime.fromtimestamp(wall).strftime("%H:%M:%S") if wall > 0 else ""
            # Dense tick spacing on small plots can round two neighboring ticks
            # to the same label (e.g. "-0.6 s" and "-1.0 s" both showing "-1 s");
            # blank the repeat instead of drawing a confusing duplicate.
            if label and label in seen:
                label = ""
            else:
                seen.add(label)
            strings.append(label)
        return strings


class PlotWidget(pg.PlotWidget):
    """Drop-in replacement for LinePlotWidget using pyqtgraph."""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(
        self,
        y_label: str,
        empty_text: str = "No data",
        on_click=None,
        absolute_time: bool = False,
        y_range: tuple[float, float] | None = None,
        min_y_range: float | None = None,
        min_x_range: float | None = None,
        monitor_mode: bool = False,
        hover_label: str | None = None,
        interactive: bool = True,
    ) -> None:
        self._wall_clock_axis = WallClockAxis(orientation="bottom")
        super().__init__(axisItems={"bottom": self._wall_clock_axis})
        self.setObjectName("linePlot")
        self.setMinimumHeight(180)
        self.y_label = y_label
        self.hover_label = hover_label or y_label
        self.empty_text = empty_text
        self.absolute_time = absolute_time
        self._y_range = y_range
        self._min_y_range = min_y_range
        self._min_x_range = min_x_range
        self._monitor_mode = monitor_mode
        self._wall_clock_axis.setRelative(not absolute_time)

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

        # Embedded/monitor plots: disable zoom, pan, context menu, and
        # auto-range buttons so mouse wheel/drag over them scrolls the page
        # instead of fighting it. Popped-out dialog plots stay interactive.
        self._interactive = interactive and not monitor_mode
        if not self._interactive:
            vb = self.plotItem.vb
            vb.setMenuEnabled(False)
            vb.setMouseEnabled(x=False, y=False)
            vb.enableAutoRange(x=False, y=False)
            self.plotItem.hideButtons()

        # Add crosshair
        self._vline = pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen(color="#888888", style=Qt.DashLine))
        self._hline = pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen(color="#888888", style=Qt.DashLine))
        self.addItem(self._vline, ignoreBounds=True)
        self.addItem(self._hline, ignoreBounds=True)
        self._vline.setVisible(False)
        self._hline.setVisible(False)

        # Plot curve
        self._curve = self.plot(pen=pg.mkPen(color="#111111", width=2), symbol=None)
        self._curves = [self._curve]
        self._legend = None
        self._series_points: list[tuple[str, list[tuple[float, float, float]]]] = []

        # Empty label
        self._empty_label = QLabel(empty_text, self)
        self._empty_label.setAlignment(Qt.AlignCenter)
        self._empty_label.setStyleSheet("color: #888888; font-size: 13px;")
        self._empty_label.setVisible(True)

        # Stats overlay (top-right)
        self._stats_label = QLabel(self)
        self._stats_label.setAlignment(Qt.AlignRight | Qt.AlignTop)
        self._stats_label.setStyleSheet(
            "color: #333333; font-size: 11px; font-family: Menlo, Consolas, monospace; "
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

    def set_y_label(self, y_label: str, hover_label: str | None = None) -> None:
        self.y_label = y_label
        self.hover_label = hover_label or y_label
        self.setLabel("left", y_label)

    def set_points(self, points: Sequence[tuple]) -> None:
        self.set_series((('', points),))

    def set_series(self, series: Sequence[tuple[str, Sequence[tuple]]]) -> None:
        """Set one or more aligned histories, keeping the inline chart compact."""
        if not series or not any(points for _label, points in series):
            self._series_points = []
            self._points = []
            self._update_empty()
            self._redraw()
            return

        raw_series = []
        for label, points in series:
            if not points:
                continue
            raw = (
                [(mono, wall, val) for mono, wall, val in points]
                if len(points[0]) == 3
                else [(x, x, y) for x, y in points]
            )
            raw_series.append((label, raw))

        if self._abs_time:
            max_wall = max(wall for _label, points in raw_series for _mono, wall, _val in points)
            self._wall_clock_axis.setWallRef(max_wall)
            self._series_points = [
                (label, [(wall - max_wall, wall, val) for _mono, wall, val in points])
                for label, points in raw_series
            ]
        else:
            max_mono = max(mono for _label, points in raw_series for mono, _wall, _val in points)
            self._series_points = [
                (label, [(mono - max_mono, wall, val) for mono, wall, val in points])
                for label, points in raw_series
            ]

        self._points = next(
            (points for _label, points in self._series_points if points),
            [],
        )
        self._update_series_legend()
        self._update_empty()
        self._redraw()

    def _update_series_legend(self) -> None:
        multiple = len(self._series_points) > 1
        if multiple and self._legend is None:
            self._legend = self.addLegend(offset=(10, 10))
        if self._legend is None:
            return
        self._legend.clear()
        self._legend.setVisible(multiple)
        for index, (label, _points) in enumerate(self._series_points):
            if index < len(self._curves):
                curve = self._curves[index]
            else:
                curve = self.plot()
                self._curves.append(curve)
            curve.setPen(pg.mkPen(color=("#111111", "#3f6f9f", "#6d8c66", "#9a6f3f")[index % 4], width=2))
            self._legend.addItem(curve, label)
        for curve in self._curves[len(self._series_points):]:
            curve.setData([], [])

    def _update_empty(self) -> None:
        point_count = sum(len(points) for _label, points in self._series_points)
        visible = point_count < 2
        self._empty_label.setVisible(visible)
        self._stats_label.setVisible(not visible)

    def _redraw(self) -> None:
        if sum(len(points) for _label, points in self._series_points) < 2:
            for curve in self._curves:
                curve.setData([], [])
            return

        x_values = [point[0] for _label, points in self._series_points for point in points]
        y_values = [point[2] for _label, points in self._series_points for point in points]
        x = np.array(x_values)
        y = np.array(y_values)

        if self._time_window_s is not None and not self._abs_time:
            max_x = x.max()
            for curve, (_label, points) in zip(self._curves, self._series_points):
                series_x = np.array([point[0] for point in points])
                series_y = np.array([point[2] for point in points])
                mask = series_x >= max_x - self._time_window_s
                curve.setData(series_x[mask], series_y[mask])
        else:
            for curve, (_label, points) in zip(self._curves, self._series_points):
                curve.setData(
                    np.array([point[0] for point in points]),
                    np.array([point[2] for point in points]),
                )

        if self._first_draw:
            self._first_draw = False
            if self._y_range is not None:
                self.enableAutoRange(x=True, y=False)
                self.setYRange(self._y_range[0], self._y_range[1], padding=0)
                self.plotItem.vb.setLimits(yMin=self._y_range[0], yMax=self._y_range[1])
            elif not self._abs_time:
                self.enableAutoRange(x=False, y=True)
                self.setXRange(x.min(), 0, padding=0)
            else:
                self.enableAutoRange(x=True, y=True)

            if self._min_y_range is not None:
                self.plotItem.vb.setLimits(minYRange=self._min_y_range)

            if self._min_x_range is not None:
                self.plotItem.vb.setLimits(minXRange=self._min_x_range)

        if self._monitor_mode:
            y_min = float(y.min())
            y_max = float(y.max())
            if self._min_y_range is not None and y_max - y_min < self._min_y_range:
                mid = (y_min + y_max) / 2
                y_min = mid - self._min_y_range / 2
                y_max = mid + self._min_y_range / 2
            if self._y_range is not None:
                y_min = max(y_min, self._y_range[0])
                y_max = min(y_max, self._y_range[1])
            self.setYRange(y_min, y_max, padding=0)

        self._update_stats()

    def _update_stats(self) -> None:
        if sum(len(points) for _label, points in self._series_points) < 2:
            return

        y = np.array([p[2] for _label, points in self._series_points for p in points])
        stats = f"min {y.min():.4g}  max {y.max():.4g}  mean {y.mean():.4g}"
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

        self._tooltip_label.setText(f"{ts}  {self.hover_label}: {val:.6g}")
        self._tooltip_label.setVisible(True)

    def leaveEvent(self, event) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._vline.setVisible(False)
        self._hline.setVisible(False)
        self._tooltip_label.setVisible(False)
