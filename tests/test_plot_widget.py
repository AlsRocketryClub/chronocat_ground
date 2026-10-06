import unittest
from types import SimpleNamespace

import math

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication

from chronocat_ground.plot_widget import PlotWidget, WallClockAxis


def wheel_accepted(plot: PlotWidget, x: float, y: float) -> bool:
    """Whether the plot keeps a wheel event; an unaccepted one scrolls the page."""
    position = QPointF(x, y)
    event = QWheelEvent(
        position, position, QPoint(0, -120), QPoint(0, -120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )
    QApplication.sendEvent(plot.viewport(), event)
    return event.isAccepted()


class PlotWidgetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def test_embedded_plot_leaves_wheel_to_page_even_over_axis_labels(self) -> None:
        plot = PlotWidget("Volts (V)", monitor_mode=True)
        plot.resize(300, 200)
        plot.show()
        self.app.processEvents()

        # pyqtgraph's AxisItem accepts wheel events over the axis labels even
        # when zoom is disabled, which stopped the page scrolling there.
        self.assertFalse(wheel_accepted(plot, 10, 80), "left axis kept the wheel")
        self.assertFalse(wheel_accepted(plot, 150, 190), "bottom axis kept the wheel")
        self.assertFalse(wheel_accepted(plot, 150, 80), "plot area kept the wheel")
        plot.close()

    def test_invalid_samples_and_long_pauses_insert_line_breaks(self) -> None:
        points = PlotWidget._points_with_gaps(
            [
                (0.0, 0.0, 10.0),
                (1.0, 1.0, math.nan),
                (2.0, 2.0, 12.0),
                (3.0, 3.0, 13.0),
                (8.0, 8.0, 14.0),
            ]
        )

        self.assertEqual([point[:2] for point in points if math.isfinite(point[2])],
                         [(0.0, 0.0), (2.0, 2.0), (3.0, 3.0), (8.0, 8.0)])
        self.assertEqual(sum(not math.isfinite(point[2]) for point in points), 3)

    def test_relative_tick_labels_keep_subsecond_ticks_distinct(self) -> None:
        axis = SimpleNamespace(_relative=True, _wall_ref=0.0)

        labels = WallClockAxis.tickStrings(
            axis, [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0], 1.0, 0.2
        )

        self.assertEqual(
            labels, ["-1.0 s", "-0.8 s", "-0.6 s", "-0.4 s", "-0.2 s", "now"]
        )
