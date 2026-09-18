from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout


class StatusIndicator(QFrame):
    """A centered subsystem name and current status value."""

    def __init__(self, title: str, status: str) -> None:
        super().__init__()
        self.setObjectName("statusIndicator")
        self.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Preferred)
        self.setMinimumWidth(128)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(1)
        layout.setAlignment(Qt.AlignCenter)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("statusIndicatorTitle")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.status_label = QLabel(status)
        self.status_label.setObjectName("statusIndicatorValue")
        self.status_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.title_label)
        layout.addWidget(self.status_label)
        self.set_status(status, "unknown")

    def set_status(self, text: str, state: str, tooltip: str = "") -> None:
        self.status_label.setText(text)
        self.setToolTip(tooltip)
        if self.property("status") != state:
            self.setProperty("status", state)
            self.style().unpolish(self)
            self.style().polish(self)
