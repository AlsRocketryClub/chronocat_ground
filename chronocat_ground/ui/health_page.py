"""Health page: active issues first, every monitored item as a tile, then the event log."""

from __future__ import annotations

from collections.abc import Sequence
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..health_model import ERROR, GROUPS, OK, WARNING, HealthEvent, HealthItem, Issue
from .widgets import Panel

_EVENT_COLORS = {
    ERROR: ("#f5d8d8", "#702525"),
    WARNING: ("#fff0c7", "#6b5200"),
    OK: ("#d8ead8", "#1f4d1f"),
}


class HealthTile(QLabel):
    """One monitored item: short label, short value, colour by state, details on hover."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("healthTile")
        self.setAlignment(Qt.AlignCenter)
        self.setFixedSize(86, 44)
        self._shown: HealthItem | None = None

    def show_item(self, item: HealthItem) -> None:
        if item == self._shown:
            return
        self._shown = item
        self.setText(f"{item.label}\n{item.value or '—'}")
        tooltip = [item.name, item.value] + ([item.reason] if item.reason else [])
        self.setToolTip("\n".join(part for part in tooltip if part))
        self.setProperty("state", item.state)
        self.style().unpolish(self)
        self.style().polish(self)


class HealthPage(QWidget):
    def __init__(self, initial_items: Sequence[HealthItem]) -> None:
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.issues_panel = Panel()
        self.issues_title = QLabel()
        self.issues_title.setObjectName("healthIssuesBanner")
        self.issues_panel.layout.addWidget(self.issues_title)
        self.issue_rows = QVBoxLayout()
        self.issue_rows.setSpacing(4)
        self.issues_panel.layout.addLayout(self.issue_rows)
        layout.addWidget(self.issues_panel)
        self._shown_issues: list[Issue] | None = None

        status_panel = Panel("STATUS")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(6)
        self.tiles: dict[str, HealthTile] = {}
        for row, group in enumerate(GROUPS):
            group_label = QLabel(group)
            group_label.setObjectName("healthGroupLabel")
            grid.addWidget(group_label, row, 0, Qt.AlignLeft | Qt.AlignVCenter)
            tiles_row = QHBoxLayout()
            tiles_row.setSpacing(4)
            subgroup = None
            for item in (item for item in initial_items if item.group == group):
                if item.subgroup and item.subgroup != subgroup:
                    subgroup = item.subgroup
                    subgroup_label = QLabel(subgroup)
                    subgroup_label.setObjectName("healthSubgroupLabel")
                    tiles_row.addWidget(subgroup_label)
                tile = HealthTile()
                self.tiles[item.key] = tile
                tiles_row.addWidget(tile)
            tiles_row.addStretch(1)
            grid.addLayout(tiles_row, row, 1)
        grid.setColumnStretch(1, 1)
        status_panel.layout.addLayout(grid)
        note = QLabel("Hover a tile for details. Grey means no current data.")
        note.setObjectName("smallNote")
        status_panel.layout.addWidget(note)
        layout.addWidget(status_panel)

        events_panel = Panel("EVENTS")
        self.event_list = QListWidget()
        self.event_list.setObjectName("healthEvents")
        self.event_list.setMinimumHeight(180)
        events_panel.layout.addWidget(self.event_list)
        layout.addWidget(events_panel)
        layout.addStretch(1)

        self.show_health(initial_items, [], [])

    def show_health(
        self,
        items: Sequence[HealthItem],
        issues: Sequence[Issue],
        new_events: Sequence[HealthEvent],
    ) -> None:
        for item in items:
            tile = self.tiles.get(item.key)
            if tile is not None:
                tile.show_item(item)
        self._show_issues(list(issues), items)
        for event in new_events:
            self._add_event(event)

    def _show_issues(self, issues: list[Issue], items: Sequence[HealthItem]) -> None:
        downlink_live = any(item.key == "downlink" and item.state == OK for item in items)
        key = (issues, downlink_live)
        if key == self._shown_issues:
            return
        self._shown_issues = key
        while self.issue_rows.count():
            widget = self.issue_rows.takeAt(0).widget()
            if widget is not None:
                # Detach now: deleteLater alone leaves the old row painted
                # under the new ones until the event loop gets to it.
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()

        if issues:
            errors = sum(issue.state == ERROR for issue in issues)
            warnings = len(issues) - errors
            counts = ", ".join(
                f"{count} {noun}{'' if count == 1 else 's'}"
                for count, noun in ((errors, "error"), (warnings, "warning"))
                if count
            )
            self.issues_title.setText(f"ACTIVE ISSUES · {counts.upper()}")
            state = ERROR if errors else WARNING
            for issue in issues:
                bullet_color = _EVENT_COLORS[issue.state][1]
                row = QLabel(f'<span style="color:{bullet_color}">■</span>&nbsp;&nbsp;{escape(issue.text)}')
                row.setObjectName("healthIssue")
                row.setTextFormat(Qt.RichText)
                row.setWordWrap(True)
                self.issue_rows.addWidget(row)
        elif downlink_live:
            self.issues_title.setText("ALL SYSTEMS NOMINAL")
            state = OK
        else:
            self.issues_title.setText("WAITING FOR TELEMETRY")
            state = "unknown"
        self.issues_title.setProperty("state", state)
        self.issues_title.style().unpolish(self.issues_title)
        self.issues_title.style().polish(self.issues_title)

    def _add_event(self, event: HealthEvent) -> None:
        entry = QListWidgetItem(f"{event.time:%H:%M:%S}   {event.text}")
        background, foreground = _EVENT_COLORS.get(event.state, ("#eeeeee", "#333333"))
        entry.setBackground(QBrush(QColor(background)))
        entry.setForeground(QBrush(QColor(foreground)))
        self.event_list.insertItem(0, entry)
        while self.event_list.count() > 200:
            self.event_list.takeItem(self.event_list.count() - 1)
