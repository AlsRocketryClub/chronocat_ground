"""Notice board restarts from telemetry and work out what caused them.

The board's uptime restarts from zero at every reset, so a drop in uptime is a
restart, and "now minus uptime" dates it even when the ground station was not
listening at the time. The watchdog flag only says whether the latest reset was
a watchdog reset; the remote reset command also resets through the watchdog,
so a restart shortly after that command is reported as commanded.
"""

from __future__ import annotations

from dataclasses import dataclass

CAUSE_WATCHDOG = "watchdog"
CAUSE_POWER = "power"
CAUSE_COMMAND = "command"

# How far the estimated reset time may sit from the reset command and still
# count as caused by it (the board resets about 0.4 s after replying).
COMMAND_WINDOW_S = 20.0
# Two estimates of one reset's time differ only by packet jitter.
SAME_RESET_TOLERANCE_S = 30.0


@dataclass(frozen=True)
class BoardReset:
    cause: str
    reset_wall: float
    previous_uptime_ms: int | None
    pid_heaters: tuple[int, ...] | None


def same_reset(first_wall: float | None, second_wall: float | None) -> bool:
    return (
        first_wall is not None
        and second_wall is not None
        and abs(first_wall - second_wall) <= SAME_RESET_TOLERANCE_S
    )


class ResetMonitor:
    def __init__(self) -> None:
        self._last_uptime_ms: int | None = None
        self._command_wall: float | None = None

    def clear(self) -> None:
        self._last_uptime_ms = None

    def note_reset_command(self, wall: float) -> None:
        self._command_wall = wall

    def update(
        self,
        uptime_ms: int,
        watchdog_flag: bool,
        wall: float,
        pid_heaters: tuple[int, ...] | None = None,
    ) -> BoardReset | None:
        """Return the restart this packet reveals, if any."""
        reset_wall = wall - uptime_ms / 1000.0
        previous = self._last_uptime_ms
        self._last_uptime_ms = uptime_ms
        if previous is None:
            # First packet: an older watchdog reset is still worth knowing about.
            if not watchdog_flag:
                return None
        elif uptime_ms >= previous:
            return None

        if self._command_wall is not None and abs(reset_wall - self._command_wall) <= COMMAND_WINDOW_S:
            cause = CAUSE_COMMAND
            self._command_wall = None
        elif watchdog_flag:
            cause = CAUSE_WATCHDOG
        else:
            cause = CAUSE_POWER
        return BoardReset(cause, reset_wall, previous, pid_heaters)


def format_ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 10:
        return "just now"
    if seconds < 60:
        return f"{seconds} s ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} h {minutes} min ago" if minutes else f"{hours} h ago"
    return f"{hours // 24} d ago"
