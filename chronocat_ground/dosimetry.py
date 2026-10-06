"""Convert Geiger count rates to dose with each detector's xDER coefficient.

The detectors are energy-compensated and individually calibrated; the
manufacturer's conversion is

    dose rate (Sv/h) = counts per second × xDER
    accumulated dose (Sv) = counts × xDER / 3600

xDER is read once per detector with the "P" command (TCP 0x50) and does not
change, so it is stored in the telemetry database.
"""

from __future__ import annotations

import math

# Packets further apart than this are a gap in the downlink, not a time step
# to integrate over.
MAX_INTEGRATION_STEP_S = 5.0


def dose_rate_usv_h(cps: float, xder: float) -> float:
    return cps * xder * 1e6


def user_dose_usv(dose_counts: float, xder: float) -> float:
    """The detector's user dose (its "Dose" field, zeroed by "reset dose"), per the
    manufacturer: accumulated dose (Sv) = counts × xDER / 3600."""
    return dose_counts * xder / 3600.0 * 1e6


def is_plausible_xder(xder: float) -> bool:
    """A usable coefficient is finite and positive; anything else cannot convert a rate."""
    return math.isfinite(xder) and xder > 0.0


class SessionDose:
    """Dose each detector has seen since the session started, integrated on the ground.

    Integrating the received dose rate needs only the coefficient and the rate,
    so it does not depend on how the detector's own accumulated fields are kept.
    """

    def __init__(self, detector_count: int = 2) -> None:
        self.dose_usv = [0.0] * detector_count
        self.covered_s = [0.0] * detector_count
        self._last: list[float | None] = [None] * detector_count

    def clear(self) -> None:
        self.dose_usv = [0.0] * len(self.dose_usv)
        self.covered_s = [0.0] * len(self.covered_s)
        self._last = [None] * len(self._last)

    def record(self, detector_id: int, monotonic_s: float, cps: float | None, xder: float | None) -> None:
        """Add the dose since the previous reading; a missing reading or coefficient breaks the chain."""
        if cps is None or xder is None or not math.isfinite(cps):
            self._last[detector_id] = None
            return
        previous = self._last[detector_id]
        self._last[detector_id] = monotonic_s
        if previous is None:
            return
        step = monotonic_s - previous
        if 0.0 < step <= MAX_INTEGRATION_STEP_S:
            self.dose_usv[detector_id] += dose_rate_usv_h(cps, xder) * step / 3600.0
            self.covered_s[detector_id] += step


def points_to_usv_h(points, xder: float) -> list[tuple[float, float, float]]:
    """Convert (monotonic, wall, cps) history points to µSv/h."""
    return [(monotonic, wall, dose_rate_usv_h(cps, xder)) for monotonic, wall, cps in points]


def points_to_user_dose_usv(points, xder: float) -> list[tuple[float, float, float]]:
    """Convert (monotonic, wall, Dose) history points to user dose in µSv."""
    return [(monotonic, wall, user_dose_usv(dose, xder)) for monotonic, wall, dose in points]


def format_dose_rate(usv_h: float) -> str:
    return f"{usv_h:.3g}"
