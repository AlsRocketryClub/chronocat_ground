"""Turn telemetry and link state into health items, active issues and events.

Every monitored thing becomes one HealthItem with a state and, when it is not
ok, a short reason. The Health page draws these items; the issue list and the
event log are derived from them, so all three always agree.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime

from .heater_safety import MAX_SAFE_TEMPERATURE_C
from .protocol import (
    AMBIENT_SENSOR_IDS,
    GEIGER_FAULT_FLAGS,
    GEIGER_INFO_FLAGS,
    GEIGER_NOTICE_FLAGS,
    HEATER_SENSOR_IDS,
    TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET,
    TELEMETRY_FLAG_SD_LOG_ACTIVE,
    TELEMETRY_FLAG_SD_LOG_ERROR,
    TEMP_SENSOR_LABELS,
    PidTelemetryPacket,
    TelemetryPacket,
    ad7177_status_names,
    geiger_error_names,
    telemetry_health_name,
)
from .sample_layout import SAMPLE_CHANNELS

OK = "ok"
WARNING = "warning"
ERROR = "error"
UNKNOWN = "unknown"

_SEVERITY = {ERROR: 0, WARNING: 1, UNKNOWN: 2, OK: 3}
STALE_AFTER_S = 2.5

GROUPS = (
    "SYSTEM", "BOARD", "TEMP F1", "TEMP F2", "SAMPLES F1", "SAMPLES F2", "GEIGER",
    "HEATERS F1", "HEATERS F2",
)
_FIRMWARE_SHORT = {0: "ok", 1: "TCP down", 2: "temp error"}
_TCP_LISTENING = 4
_FIRMWARE_TCP_NOT_LISTENING = 1


@dataclass(frozen=True)
class HealthItem:
    key: str
    group: str
    label: str
    name: str
    state: str
    value: str = ""
    reason: str = ""
    subgroup: str = ""
    # Groups whose own items already explain this problem when they show one.
    explained_by: str = ""
    # Extra context for the tooltip only; tiles show just the short value.
    detail: str = ""

    @property
    def is_problem(self) -> bool:
        return self.state in (ERROR, WARNING)


@dataclass(frozen=True)
class LinkStatus:
    uplink_connected: bool
    downlink: str  # "waiting", "receiving", "stale" or "error"
    downlink_detail: str = ""
    packet_age_s: float | None = None


@dataclass(frozen=True)
class Issue:
    state: str
    text: str


@dataclass(frozen=True)
class HealthEvent:
    time: datetime
    state: str
    text: str


def link_status(uplink_connected: bool, packet_age_s: float | None, receiver_error: str = "") -> LinkStatus:
    if receiver_error:
        return LinkStatus(uplink_connected, "error", receiver_error, packet_age_s)
    if packet_age_s is None:
        return LinkStatus(uplink_connected, "waiting", "", None)
    downlink = "receiving" if packet_age_s < STALE_AFTER_S else "stale"
    return LinkStatus(uplink_connected, downlink, "", packet_age_s)


def evaluate_health(
    packet: TelemetryPacket | None,
    pid: PidTelemetryPacket | None,
    link: LinkStatus,
) -> tuple[HealthItem, ...]:
    """Every monitored item in display order. Without a live downlink the
    packet-derived items are UNKNOWN: their last values can no longer be trusted."""
    live = packet is not None and link.downlink == "receiving"
    items = list(_link_items(link))
    items += _system_items(packet if live else None)
    items += _board_items(packet if live else None)
    items += _temperature_items(packet if live else None)
    items += _sample_items(packet if live else None)
    items += _geiger_items(packet if live else None)
    items += _heater_items(pid if live else None)
    return tuple(items)


def _link_items(link: LinkStatus) -> list[HealthItem]:
    uplink = HealthItem(
        "uplink", "SYSTEM", "Uplink", "Uplink",
        OK if link.uplink_connected else WARNING,
        "connected" if link.uplink_connected else "disconnected",
        "" if link.uplink_connected else "not connected; commands unavailable",
    )
    if link.downlink == "receiving":
        downlink = HealthItem("downlink", "SYSTEM", "Downlink", "Downlink", OK, "receiving")
    elif link.downlink == "stale":
        age = f"{link.packet_age_s:.1f} s" if link.packet_age_s is not None else "a while"
        downlink = HealthItem(
            "downlink", "SYSTEM", "Downlink", "Downlink", WARNING, "stale",
            f"no packet for {age}",
        )
    elif link.downlink == "error":
        downlink = HealthItem(
            "downlink", "SYSTEM", "Downlink", "Downlink", ERROR, "error", link.downlink_detail,
        )
    else:
        downlink = HealthItem("downlink", "SYSTEM", "Downlink", "Downlink", UNKNOWN, "waiting")
    return [uplink, downlink]


def _system_items(packet: TelemetryPacket | None) -> list[HealthItem]:
    if packet is None:
        return [
            HealthItem("firmware", "SYSTEM", "Firmware", "Firmware health", UNKNOWN),
            HealthItem("tcp", "SYSTEM", "TCP", "TCP server", UNKNOWN),
            HealthItem("sd", "SYSTEM", "SD log", "SD log", UNKNOWN),
        ]
    health = telemetry_health_name(packet.health_code)
    if packet.health_code == 0:
        firmware_state = OK
    elif packet.health_code == _FIRMWARE_TCP_NOT_LISTENING:
        firmware_state = ERROR
    else:
        # The specific sensor shows up in its own tile; this is the summary flag.
        firmware_state = WARNING
    firmware = HealthItem(
        "firmware", "SYSTEM", "Firmware", "Firmware health", firmware_state,
        _FIRMWARE_SHORT.get(packet.health_code, health),
        "" if firmware_state == OK else f"reports {health}",
        explained_by="TEMP" if packet.health_code == 2 else "",
    )
    listening = packet.tcp_status == _TCP_LISTENING
    tcp = HealthItem(
        "tcp", "SYSTEM", "TCP", "TCP server", OK if listening else ERROR,
        "listening" if listening else "not listening",
        "" if listening else "not listening; uplink commands will fail",
    )
    if packet.flags & TELEMETRY_FLAG_SD_LOG_ERROR:
        sd = HealthItem("sd", "SYSTEM", "SD log", "SD log", ERROR, "error", "write error; onboard log stopped")
    elif packet.flags & TELEMETRY_FLAG_SD_LOG_ACTIVE:
        sd = HealthItem("sd", "SYSTEM", "SD log", "SD log", OK, "logging")
    else:
        sd = HealthItem("sd", "SYSTEM", "SD log", "SD log", WARNING, "off", "not logging (no card?)")
    return [firmware, tcp, sd]


def format_uptime(milliseconds: int) -> str:
    seconds = milliseconds // 1000
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"


def _board_items(packet: TelemetryPacket | None) -> list[HealthItem]:
    if packet is None:
        return [
            HealthItem("uptime", "BOARD", "Uptime", "Board uptime", UNKNOWN),
            HealthItem("reset", "BOARD", "Last reset", "Last board reset", UNKNOWN),
        ]
    watchdog = bool(packet.flags & TELEMETRY_FLAG_PREVIOUS_WATCHDOG_RESET)
    return [
        HealthItem("uptime", "BOARD", "Uptime", "Board uptime", OK, format_uptime(packet.timestamp)),
        HealthItem(
            "reset", "BOARD", "Last reset", "Last board reset",
            WARNING if watchdog else OK,
            "watchdog" if watchdog else "normal",
            "board was restarted by the watchdog" if watchdog else "",
        ),
    ]


def format_rate(cps: float) -> str:
    """Count rate in at most ~5 characters, without scientific notation."""
    if cps >= 1000:
        return f"{cps:.0f}"
    if cps >= 10:
        return f"{cps:.1f}"
    return f"{cps:.3g}" if cps >= 0.001 else f"{cps:.4f}"


def temperature_name(sensor_id: int) -> str:
    label = TEMP_SENSOR_LABELS[sensor_id]
    if sensor_id in AMBIENT_SENSOR_IDS:
        return f"{label} (ambient)"
    return f"{label} (H{HEATER_SENSOR_IDS.index(sensor_id)})"


def _temperature_items(packet: TelemetryPacket | None) -> list[HealthItem]:
    items = []
    for board in ("F1", "F2"):
        sensors = sorted(
            (index for index, label in enumerate(TEMP_SENSOR_LABELS) if label.startswith(board + "_")),
            key=lambda index: TEMP_SENSOR_LABELS[index],
        )
        for sensor_id in sensors:
            label = TEMP_SENSOR_LABELS[sensor_id].split("_", 1)[1]
            key, group, name = f"temp:{sensor_id}", f"TEMP {board}", temperature_name(sensor_id)
            if packet is None:
                items.append(HealthItem(key, group, label, name, UNKNOWN))
                continue
            temperature = packet.temperature_c(sensor_id)
            if temperature is None:
                items.append(HealthItem(key, group, label, name, WARNING, "—", "no reading"))
            elif temperature >= MAX_SAFE_TEMPERATURE_C:
                items.append(HealthItem(
                    key, group, label, name, ERROR, f"{temperature:.1f} °C",
                    f"{temperature:.1f} °C, over the {MAX_SAFE_TEMPERATURE_C:.0f} °C limit",
                ))
            else:
                items.append(HealthItem(key, group, label, name, OK, f"{temperature:.1f} °C"))
    return items


def _sample_items(packet: TelemetryPacket | None) -> list[HealthItem]:
    items = []
    readings = {reading.slot: reading for reading in packet.ad7177_readings} if packet else {}
    for slot in sorted(SAMPLE_CHANNELS):
        channel = SAMPLE_CHANNELS[slot]
        key, label, subgroup = f"adc:{slot}", f"CH{channel.channel_index}", f"ADC{channel.adc_index}"
        group = "SAMPLES F1" if channel.adc_index < 2 else "SAMPLES F2"
        name = f"{channel.location} ({channel.name})"
        reading = readings.get(slot)
        if reading is None:
            items.append(HealthItem(key, group, label, name, UNKNOWN, subgroup=subgroup))
        elif reading.has_error:
            items.append(HealthItem(
                key, group, label, name, ERROR, f"{reading.voltage:.4f} V",
                ad7177_status_names(reading.status), subgroup,
            ))
        elif not packet.os_adc_valid(slot):
            items.append(HealthItem(key, group, label, name, WARNING, "—", "no data", subgroup))
        else:
            items.append(HealthItem(key, group, label, name, OK, f"{reading.voltage:.4f} V", "", subgroup))
    return items


def _geiger_items(packet: TelemetryPacket | None) -> list[HealthItem]:
    items = []
    for counter_id in range(2):
        key, label, name = f"geiger:{counter_id}", f"G{counter_id + 1}", f"Geiger {counter_id + 1}"
        if packet is None:
            items.append(HealthItem(key, "GEIGER", label, name, UNKNOWN))
            continue
        reading = packet.geiger_reading(counter_id)
        if reading is None or not reading.valid:
            items.append(HealthItem(key, "GEIGER", label, name, WARNING, "—", "no response"))
            continue
        value = f"{format_rate(reading.dose_rate_cps)} cps"
        detail = f"HV {reading.hv_voltage} V"
        faults = reading.error_flags & GEIGER_FAULT_FLAGS
        notices = reading.error_flags & GEIGER_NOTICE_FLAGS
        info = reading.error_flags & GEIGER_INFO_FLAGS
        if faults:
            state, reason = ERROR, geiger_error_names(faults)
        elif notices:
            state, reason = WARNING, geiger_error_names(notices)
        else:
            state, reason = OK, ""
        if info:
            detail += f"; {geiger_error_names(info)} (normal)"
        items.append(HealthItem(key, "GEIGER", label, name, state, value, reason, detail=detail))
    return items


def _heater_items(pid: PidTelemetryPacket | None) -> list[HealthItem]:
    items = []
    for heater_id in range(len(HEATER_SENSOR_IDS)):
        key, label = f"heater:{heater_id}", f"H{heater_id}"
        # One row per board (H0-H5 on F1, H6-H11 on F2), like the sensors.
        group = "HEATERS F1" if heater_id < len(HEATER_SENSOR_IDS) // 2 else "HEATERS F2"
        name = f"H{heater_id} ({TEMP_SENSOR_LABELS[HEATER_SENSOR_IDS[heater_id]]})"
        if pid is None or heater_id >= len(pid.heaters):
            items.append(HealthItem(key, group, label, name, UNKNOWN))
            continue
        reading = pid.heaters[heater_id]
        value = f"{reading.duty_permille / 10.0:.0f}%"
        if reading.result >= 7:
            items.append(HealthItem(key, group, label, name, ERROR, value, reading.result_name))
        elif reading.result >= 5 or not reading.sensor_mapped:
            items.append(HealthItem(key, group, label, name, WARNING, value, reading.result_name))
        elif not reading.sensor_valid:
            items.append(HealthItem(key, group, label, name, WARNING, value, "no sensor reading; held off"))
        else:
            items.append(HealthItem(key, group, label, name, OK, value))
    return items


def active_issues(items: tuple[HealthItem, ...]) -> list[Issue]:
    """Problems worst first. An ADC whose channels all fail the same way is one issue."""
    issues: list[tuple[int, int, Issue]] = []
    order = {item.key: index for index, item in enumerate(items)}
    handled: set[str] = set()

    by_chip: dict[str, list[HealthItem]] = {}
    for item in items:
        if item.group.startswith("SAMPLES"):
            by_chip.setdefault(item.subgroup, []).append(item)
    for chip, channels in by_chip.items():
        if all(channel.is_problem for channel in channels) and len({c.reason for c in channels}) == 1:
            reason = channels[0].reason
            text = f"{chip} not responding (all {len(channels)} channels)" if reason == "no data" else f"{chip}: {reason} on all {len(channels)} channels"
            issues.append((_SEVERITY[channels[0].state], order[channels[0].key], Issue(channels[0].state, text)))
            handled.update(channel.key for channel in channels)

    explained = {
        item.key for item in items
        if item.explained_by
        and any(other.is_problem and other.group.startswith(item.explained_by) for other in items)
    }
    for item in items:
        if item.is_problem and item.key not in handled and item.key not in explained:
            issues.append((_SEVERITY[item.state], order[item.key], Issue(item.state, f"{item.name}: {item.reason}")))
    return [issue for _severity, _order, issue in sorted(issues, key=lambda entry: entry[:2])]


class HealthEventLog:
    """Records each item's change between ok and a problem, plus board restarts.

    UNKNOWN never produces an event (it only means the downlink is stale); the
    downlink item reports that itself, and the last known state is kept so data
    coming back does not log a spurious recovery.
    """

    def __init__(self, limit: int = 200) -> None:
        self.events: deque[HealthEvent] = deque(maxlen=limit)
        self._last_state: dict[str, HealthItem] = {}
        self._last_uptime_ms: int | None = None

    def clear(self) -> None:
        self.events.clear()
        self._last_state.clear()
        self._last_uptime_ms = None

    def update(
        self, items: tuple[HealthItem, ...], now: datetime, uptime_ms: int | None = None
    ) -> list[HealthEvent]:
        new_events = []
        if uptime_ms is not None:
            if self._last_uptime_ms is not None and uptime_ms < self._last_uptime_ms:
                new_events.append(HealthEvent(now, WARNING, f"Board restarted (uptime {format_uptime(uptime_ms)})"))
            self._last_uptime_ms = uptime_ms

        for item in items:
            if item.state == UNKNOWN:
                continue
            previous = self._last_state.get(item.key)
            self._last_state[item.key] = item
            if previous is None:
                if item.is_problem:
                    new_events.append(HealthEvent(now, item.state, f"{item.name}: {item.reason}"))
                continue
            if item.is_problem and (not previous.is_problem or previous.state != item.state or previous.reason != item.reason):
                new_events.append(HealthEvent(now, item.state, f"{item.name}: {item.reason}"))
            elif previous.is_problem and not item.is_problem:
                new_events.append(HealthEvent(now, OK, f"{item.name} ok ({item.value})"))

        self.events.extend(new_events)
        return new_events


class PacketLossTracker:
    """Counts packets the board sent but the ground never received.

    The board numbers every packet; a jump in the counter means packets were
    lost. A counter that goes backwards means the board restarted, which is
    not loss, so counting simply continues from the new value.
    """

    def __init__(self) -> None:
        self.received = 0
        self.lost = 0
        self._last_counter: int | None = None

    def clear(self) -> None:
        self.received = 0
        self.lost = 0
        self._last_counter = None

    def record(self, counter: int) -> None:
        if self._last_counter is not None and counter > self._last_counter:
            self.lost += counter - self._last_counter - 1
        self.received += 1
        self._last_counter = counter

    @property
    def loss_percent(self) -> float:
        sent = self.received + self.lost
        return 100.0 * self.lost / sent if sent else 0.0
