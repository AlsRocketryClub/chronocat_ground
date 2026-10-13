"""Readable voltages: V, mV or µV, whichever suits the value.

Each channel keeps its unit until the value is clearly in another range, so a
reading hovering around 1 mV does not flip between "+0.998 mV" and "+998.0 µV".
One shared formatter is used by every page, so a channel reads the same
everywhere.
"""

from __future__ import annotations

import math
from collections.abc import Hashable

# (unit, scale, lower bound of the range, digits after the point)
_UNITS = (
    ("V", 1.0, 1.0, 4),
    ("mV", 1e-3, 1e-3, 3),
    ("µV", 1e-6, 0.0, 1),
)
# How far outside its own range a value may go before the unit changes.
_HYSTERESIS = 1.25


def _natural_unit(magnitude: float) -> int:
    for index, (_name, _scale, lower, _digits) in enumerate(_UNITS):
        if magnitude >= lower:
            return index
    return len(_UNITS) - 1


def _fits(index: int, magnitude: float) -> bool:
    lower = _UNITS[index][2]
    upper = _UNITS[index - 1][2] if index > 0 else math.inf
    return lower / _HYSTERESIS <= magnitude < upper * _HYSTERESIS


def format_volts(volts: float, unit_index: int | None = None) -> str:
    index = _natural_unit(abs(volts)) if unit_index is None else unit_index
    name, scale, _lower, digits = _UNITS[index]
    return f"{volts / scale:+.{digits}f} {name}"


class VoltFormatter:
    def __init__(self) -> None:
        self._units: dict[Hashable, int] = {}

    def format(self, key: Hashable, volts: float) -> str:
        magnitude = abs(volts)
        current = self._units.get(key)
        if current is None or not _fits(current, magnitude):
            current = _natural_unit(magnitude)
            self._units[key] = current
        return format_volts(volts, current)


# Keyed by ADC slot; shared so the Dashboard, Boards, Samples and Health agree.
VOLTS = VoltFormatter()
