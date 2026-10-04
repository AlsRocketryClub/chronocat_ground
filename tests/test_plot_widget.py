import unittest
from types import SimpleNamespace

import math

from chronocat_ground.plot_widget import PlotWidget, WallClockAxis


class PlotWidgetTest(unittest.TestCase):
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
