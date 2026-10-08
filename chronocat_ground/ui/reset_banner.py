"""Banner for board restarts: stays until the operator closes it."""

from __future__ import annotations

from datetime import datetime
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ..health_model import format_uptime
from ..reset_monitor import CAUSE_COMMAND, CAUSE_WATCHDOG, BoardReset, format_ago

_HEADLINES = {
    CAUSE_WATCHDOG: "Board reset by the watchdog",
    CAUSE_COMMAND: "Board restarted by your reset command",
}
_POWER_HEADLINE = "Board restarted (power loss or reset button)"


def _clock(wall: float, now: float) -> str:
    moment = datetime.fromtimestamp(wall)
    if moment.date() == datetime.fromtimestamp(now).date():
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%b %d %H:%M:%S")


def _heater_text(reset: BoardReset) -> str:
    if reset.pid_heaters is None:
        return ""
    if not reset.pid_heaters:
        return "No heaters in PID."
    return "PID running on " + ", ".join(f"H{heater}" for heater in reset.pid_heaters) + "."


class ResetBanner(QFrame):
    dismissed = Signal(float)
    clicked = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("resetBanner")
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to open the Health page event log")
        self.resets: list[BoardReset] = []

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 8, 8)
        layout.setSpacing(10)
        icon = QLabel("⚠")
        icon.setObjectName("resetBannerIcon")
        layout.addWidget(icon, 0, Qt.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.headline = QLabel()
        self.headline.setObjectName("resetBannerHeadline")
        self.detail = QLabel()
        self.detail.setObjectName("resetBannerDetail")
        self.detail.setWordWrap(True)
        text.addWidget(self.headline)
        text.addWidget(self.detail)
        layout.addLayout(text, 1)
        close = QPushButton("✕")
        close.setObjectName("resetBannerClose")
        close.setToolTip("Dismiss")
        close.setFixedSize(28, 28)
        close.clicked.connect(self.dismiss)
        layout.addWidget(close, 0, Qt.AlignTop)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self.setVisible(False)

    def add_reset(self, reset: BoardReset) -> None:
        self.resets.append(reset)
        kind = "info" if all(item.cause == CAUSE_COMMAND for item in self.resets) else "warning"
        if self.property("kind") != kind:
            self.setProperty("kind", kind)
            self.style().unpolish(self)
            self.style().polish(self)
        self.refresh()
        self.setVisible(True)
        self._timer.start(1000)

    def refresh(self, now: float | None = None) -> None:
        if not self.resets:
            return
        now = time.time() if now is None else now
        latest = self.resets[-1]
        headline = _HEADLINES.get(latest.cause, _POWER_HEADLINE)
        when = f"{_clock(latest.reset_wall, now)} ({format_ago(now - latest.reset_wall)})"
        if len(self.resets) == 1:
            self.headline.setText(f"{headline} at {when}")
        else:
            first = _clock(self.resets[0].reset_wall, now)
            self.headline.setText(
                f"{len(self.resets)} board resets since {first}, latest at {when}: "
                f"{headline[0].lower()}{headline[1:]}"
            )
        parts = []
        if latest.previous_uptime_ms is not None:
            parts.append(f"It had been running for {format_uptime(latest.previous_uptime_ms)} before that.")
        if heaters := _heater_text(latest):
            parts.append(heaters)
        self.detail.setText("  ".join(parts))
        self.detail.setVisible(bool(parts))

    def dismiss(self) -> None:
        if self.resets:
            self.dismissed.emit(self.resets[-1].reset_wall)
        self.resets.clear()
        self._timer.stop()
        self.setVisible(False)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)
