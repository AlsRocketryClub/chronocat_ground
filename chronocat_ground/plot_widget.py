from __future__ import annotations

import math
import time
from datetime import datetime
from collections.abc import Sequence

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QWidget


PLOT_GAP_SECONDS = 2.5


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

    def tickValues(self, minVal, maxVal, size):  # noqa: N802
        levels = super().tickValues(minVal, maxVal, size)
        # Relative labels are rounded seconds, so minor tick levels can
        # produce multiple ticks with the same visible label.
        return levels[:1] if self._relative else levels

    def tickStrings(self, values, scale, spacing):  # noqa: N802
        strings = []
        seen: set[str] = set()
        for v in values:
            if self._relative:
                if abs(v) < max(abs(spacing) * 1e-6, 1e-9):
                    label = "now"
                else:
                    decimals = max(0, math.ceil(-math.log10(abs(spacing))))
                    label = f"{v:.{decimals}f} s"
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


SERIES_COLORS = ("#111111", "#3f6f9f", "#6d8c66", "#9a6f3f", "#8a4f8a", "#b05050")


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
        legend: bool = True,
    ) -> None:
        self._show_legend = legend
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
        self._monitor_y_range: tuple[float, float] | None = None
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
        self._band_points: list = []
        self._bands: list = []
        self._last_series_args: tuple = ((), None)

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
        self.hover_label = hover_label or y_label
        # setLabel relayouts the plot, so only touch it when the label changes.
        if y_label != self.y_label:
            self.y_label = y_label
            self.setLabel("left", y_label)

    def set_points(self, points: Sequence[tuple]) -> None:
        self.set_series((('', points),))

    def set_time_window(self, seconds: float | None) -> None:
        """Show only the newest `seconds` of history (None shows everything held)."""
        self._time_window_s = seconds
        self.set_series(*self._last_series_args)

    def set_series(
        self,
        series: Sequence[tuple[str, Sequence[tuple]]],
        bands: dict[str, Sequence[tuple]] | None = None,
    ) -> None:
        """Set one or more aligned histories, keeping the inline chart compact.

        `bands` maps a series label to its ± error history (same timestamps as the
        series); each is drawn as a shaded band around that series.
        """
        self._last_series_args = (series, bands)
        if not series or not any(points for _label, points in series):
            self._series_points = []
            self._band_points = []
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

        if not any(math.isfinite(val) for _label, points in raw_series for _mono, _wall, val in points):
            self._series_points = []
            self._band_points = []
            self._points = []
            self._update_empty()
            self._redraw()
            return

        if self._abs_time:
            max_wall = max(wall for _label, points in raw_series for _mono, wall, val in points if math.isfinite(val))
            self._wall_clock_axis.setWallRef(max_wall)

            def relative(points):
                return self._points_with_gaps([(wall - max_wall, wall, val) for _mono, wall, val in points])
        else:
            max_mono = max(mono for _label, points in raw_series for mono, _wall, val in points if math.isfinite(val))
            window = self._time_window_s

            def relative(points):
                shifted = [(mono - max_mono, wall, val) for mono, wall, val in points]
                if window is not None:
                    shifted = [point for point in shifted if point[0] >= -window]
                return self._points_with_gaps(shifted)

        self._series_points = [(label, relative(points)) for label, points in raw_series]
        self._band_points = []
        for index, (label, points) in enumerate(raw_series):
            errors = (bands or {}).get(label)
            if not errors or len(errors) != len(points):
                continue
            upper = [(m, w, v + e[2]) for (m, w, v), e in zip(points, errors)]
            lower = [(m, w, v - e[2]) for (m, w, v), e in zip(points, errors)]
            self._band_points.append((index, relative(upper), relative(lower)))

        self._points = next(
            (
                valid_points
                for _label, points in self._series_points
                if (valid_points := [point for point in points if math.isfinite(point[2])])
            ),
            [],
        )
        self._update_series_legend()
        self._update_empty()
        self._redraw()

    @staticmethod
    def _points_with_gaps(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
        """Insert NaN separators so invalid samples and long outages break lines."""
        result: list[tuple[float, float, float]] = []
        previous_valid: tuple[float, float, float] | None = None
        gap_pending = False
        for point in points:
            if not math.isfinite(point[2]):
                if previous_valid is not None:
                    gap_pending = True
                result.append(point)
                continue
            if previous_valid is not None and (gap_pending or point[0] - previous_valid[0] > PLOT_GAP_SECONDS):
                result.append((point[0], point[1], math.nan))
            result.append(point)
            previous_valid = point
            gap_pending = False
        return result

    def _update_series_legend(self) -> None:
        # One curve per series, whether or not a legend is shown.
        for index, _series in enumerate(self._series_points):
            if index >= len(self._curves):
                self._curves.append(self.plot())
            self._curves[index].setPen(pg.mkPen(color=SERIES_COLORS[index % len(SERIES_COLORS)], width=2))
        for curve in self._curves[len(self._series_points):]:
            curve.setData([], [])

        multiple = len(self._series_points) > 1 and self._show_legend
        if multiple and self._legend is None:
            self._legend = self.addLegend(offset=(10, 10))
        if self._legend is None:
            return
        self._legend.clear()
        self._legend.setColumnCount(3 if len(self._series_points) > 4 else 1)
        self._legend.setVisible(multiple)
        for curve, (label, _points) in zip(self._curves, self._series_points):
            self._legend.addItem(curve, label)

    def _draw_bands(self) -> None:
        while len(self._bands) < len(self._band_points):
            upper, lower = pg.PlotDataItem(), pg.PlotDataItem()
            fill = pg.FillBetweenItem(upper, lower)
            fill.setZValue(-10)
            self.addItem(fill)
            self._bands.append((upper, lower, fill))
        for index, (upper, lower, fill) in enumerate(self._bands):
            if index >= len(self._band_points):
                upper.setData([], [])
                lower.setData([], [])
                continue
            series_index, upper_points, lower_points = self._band_points[index]
            for item, points in ((upper, upper_points), (lower, lower_points)):
                item.setData(
                    np.array([point[0] for point in points]),
                    np.array([point[2] for point in points]),
                    connect="finite",
                )
            color = QColor(SERIES_COLORS[series_index % len(SERIES_COLORS)])
            color.setAlpha(45)
            fill.setBrush(pg.mkBrush(color))
            fill.setPen(pg.mkPen(None))

    def _update_empty(self) -> None:
        point_count = sum(sum(math.isfinite(point[2]) for point in points) for _label, points in self._series_points)
        visible = point_count < 2
        self._empty_label.setVisible(visible)
        self._stats_label.setVisible(not visible)

    def _redraw(self) -> None:
        finite_points = [point for _label, points in self._series_points for point in points if math.isfinite(point[2])]
        if len(finite_points) < 2:
            for curve in self._curves:
                curve.setData([], [])
            self._band_points = []
            self._draw_bands()
            return

        x_values = [point[0] for point in finite_points]
        y_values = [point[2] for point in finite_points]
        x = np.array(x_values)
        y = np.array(y_values)

        if self._time_window_s is not None and not self._abs_time:
            max_x = x.max()
            for curve, (_label, points) in zip(self._curves, self._series_points):
                series_x = np.array([point[0] for point in points])
                series_y = np.array([point[2] for point in points])
                mask = (series_x >= max_x - self._time_window_s) | ~np.isfinite(series_y)
                curve.setData(series_x[mask], series_y[mask], connect="finite")
        else:
            for curve, (_label, points) in zip(self._curves, self._series_points):
                curve.setData(
                    np.array([point[0] for point in points]),
                    np.array([point[2] for point in points]),
                    connect="finite",
                )

        self._draw_bands()

        if self._first_draw:
            self._first_draw = False
            if self._y_range is not None:
                self.enableAutoRange(x=True, y=False)
                self.setYRange(self._y_range[0], self._y_range[1], padding=0)
                self.plotItem.vb.setLimits(yMin=self._y_range[0], yMax=self._y_range[1])
            elif not self._abs_time:
                self.enableAutoRange(x=True, y=True)
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
            if (y_min, y_max) != self._monitor_y_range:
                self._monitor_y_range = (y_min, y_max)
                self.setYRange(y_min, y_max, padding=0)

        self._update_stats()

    def _update_stats(self) -> None:
        values = [p[2] for _label, points in self._series_points for p in points if math.isfinite(p[2])]
        if len(values) < 2:
            return

        y = np.array(values)
        stats = f"min {y.min():.4g}  max {y.max():.4g}  mean {y.mean():.4g}"
        self._stats_label.setText(stats)
        self._place_stats_label()

    def _place_stats_label(self) -> None:
        """Pin the stats overlay top-right, sized to its text so nothing is cut off."""
        self._stats_label.adjustSize()
        width = min(self._stats_label.width(), max(self.width() - 8, 0))
        self._stats_label.setGeometry(
            self.width() - width - 4, 4, width, self._stats_label.height()
        )

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if event is None:
            return
        w, h = event.size().width(), event.size().height()
        self._empty_label.setGeometry(0, 0, w, h)
        self._place_stats_label()
        self._tooltip_label.setGeometry(4, 4, 200, 40)

    def wheelEvent(self, event) -> None:  # noqa: N802
        # Embedded plots never zoom, so hand the wheel straight to the page.
        # Going through pyqtgraph instead loses it over the axes: AxisItem
        # accepts the event even when its ViewBox has mouse zoom disabled.
        if not self._interactive:
            event.ignore()
            return
        super().wheelEvent(event)

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


class _HistorySeries:
    def __init__(self) -> None:
        self.points = np.empty((8192, 2), dtype=float)
        self.length = 0
        self.count = 0
        self.value_sum = 0.0
        self.value_min = math.inf
        self.value_max = -math.inf
        self.last_wall: float | None = None


class HistoryPlotWidget(PlotWidget):
    """Full-history plot with amortized appends and view-dependent peak reduction."""

    def __init__(self, *args, series_names: tuple[str, ...] = ("",), **kwargs) -> None:
        super().__init__(*args, absolute_time=True, **kwargs)
        self._history_series = [_HistorySeries() for _name in series_names]
        self._history_names = series_names
        self._origin: float | None = None
        if len(series_names) > 1:
            self._legend = self.addLegend(offset=(10, 10))
        for index, name in enumerate(series_names):
            curve = self._curve if index == 0 else self.plot()
            if index:
                self._curves.append(curve)
            curve.setPen(pg.mkPen(color=("#111111", "#3f6f9f")[index % 2], width=2))
            curve.setClipToView(True)
            curve.setDownsampling(auto=True, method="peak")
            if self._legend is not None:
                self._legend.addItem(curve, name)

    @property
    def sample_count(self) -> int:
        return sum(series.count for series in self._history_series)

    def append_history(self, points: np.ndarray, series_index: int = 0) -> None:
        if not len(points):
            return
        series = self._history_series[series_index]
        if self._origin is None:
            # Keep this fixed: new data must not shift a user's historical viewport.
            self._origin = float(points[0, 0])
            self._wall_clock_axis.setWallRef(self._origin)
        walls = points[:, 0]
        previous = np.concatenate((
            [series.last_wall if series.last_wall is not None else walls[0]], walls[:-1]
        ))
        gaps = (walls - previous) > PLOT_GAP_SECONDS
        positions = np.arange(len(points)) + np.cumsum(gaps)
        count = len(points) + int(gaps.sum())
        needed = series.length + count
        if needed > len(series.points):
            capacity = max(needed, len(series.points) * 2)
            grown = np.empty((capacity, 2), dtype=float)
            grown[:series.length] = series.points[:series.length]
            series.points = grown
        batch = series.points[series.length:needed]
        batch[:, 0] = math.nan
        batch[:, 1] = math.nan
        batch[positions, 0] = walls - self._origin
        batch[positions, 1] = points[:, 1]
        # Give each line-break a finite x coordinate, but a NaN y coordinate.
        batch[positions[gaps] - 1, 0] = walls[gaps] - self._origin
        series.length = needed
        series.last_wall = float(walls[-1])

        values = points[:, 1][np.isfinite(points[:, 1])]
        if len(values):
            series.count += len(values)
            series.value_sum += float(values.sum())
            series.value_min = min(series.value_min, float(values.min()))
            series.value_max = max(series.value_max, float(values.max()))
        data = series.points[:series.length]
        self._curves[series_index].setData(data[:, 0], data[:, 1], connect="finite")
        self._empty_label.setVisible(self.sample_count < 2)
        self._stats_label.setVisible(self.sample_count >= 2)
        if self.sample_count:
            minimum = min(item.value_min for item in self._history_series)
            maximum = max(item.value_max for item in self._history_series)
            total = sum(item.value_sum for item in self._history_series)
            self._stats_label.setText(
                f"min {minimum:.4g}  max {maximum:.4g}  "
                f"mean {total / self.sample_count:.4g}"
            )

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        # Bypass the rolling plot's list-based nearest-point scan.
        pg.PlotWidget.mouseMoveEvent(self, event)
        if self.sample_count < 2:
            return
        mouse = self.plotItem.vb.mapSceneToView(event.position())
        readings = []
        for name, series in zip(self._history_names, self._history_series):
            if not series.length:
                continue
            data = series.points[:series.length]
            index = min(int(np.searchsorted(data[:, 0], mouse.x())), len(data) - 1)
            if index > 0 and abs(data[index - 1, 0] - mouse.x()) < abs(data[index, 0] - mouse.x()):
                index -= 1
            x, value = data[index]
            if math.isfinite(value):
                stamp = datetime.fromtimestamp(self._origin + x).strftime("%Y-%m-%d %H:%M:%S")
                readings.append(f"{stamp}  {name or self.hover_label}: {value:.6g}")
        if not readings:
            self._tooltip_label.setVisible(False)
            return
        self._vline.setVisible(True)
        self._hline.setVisible(True)
        self._vline.setPos(mouse.x())
        self._hline.setPos(mouse.y())
        self._tooltip_label.setText("\n".join(readings))
        self._tooltip_label.adjustSize()
        self._tooltip_label.setVisible(True)
