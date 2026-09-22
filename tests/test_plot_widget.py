import unittest
from types import SimpleNamespace

from chronocat_ground.plot_widget import WallClockAxis


class PlotWidgetTest(unittest.TestCase):
    def test_relative_tick_labels_keep_subsecond_ticks_distinct(self) -> None:
        axis = SimpleNamespace(_relative=True, _wall_ref=0.0)

        labels = WallClockAxis.tickStrings(
            axis, [-1.0, -0.8, -0.6, -0.4, -0.2, 0.0], 1.0, 0.2
        )

        self.assertEqual(
            labels, ["-1.0 s", "-0.8 s", "-0.6 s", "-0.4 s", "-0.2 s", "now"]
        )
