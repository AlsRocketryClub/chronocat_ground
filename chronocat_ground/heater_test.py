from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .protocol import (
    CHARACTERIZATION_BASELINE,
    CHARACTERIZATION_COOLDOWN,
    CHARACTERIZATION_HEATING,
    CHARACTERIZATION_IDLE,
    CHARACTERIZATION_STATE_NAMES,
    CommandResponse,
    TelemetryPacket,
)

HEATER_TEST_COUNT = 12
HEATER_TEST_MAX_DUTY = 20
HEATER_TEST_HISTORY_LENGTH = 3600


@dataclass
class HeaterTestSession:
    selected_heater: int | None = None
    requested_duty: int = 0
    state: int = CHARACTERIZATION_IDLE
    start_timestamp_ms: int | None = None
    last_timestamp_ms: int | None = None
    last_status_error: str | None = None
    temperature_histories: list[deque[tuple[float, float]]] = field(
        default_factory=lambda: [deque(maxlen=HEATER_TEST_HISTORY_LENGTH) for _ in range(HEATER_TEST_COUNT)]
    )
    validity_histories: list[deque[tuple[float, bool]]] = field(
        default_factory=lambda: [deque(maxlen=HEATER_TEST_HISTORY_LENGTH) for _ in range(HEATER_TEST_COUNT)]
    )
    duty_history: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=HEATER_TEST_HISTORY_LENGTH)
    )

    def reset_histories(self) -> None:
        for history in self.temperature_histories:
            history.clear()
        for history in self.validity_histories:
            history.clear()
        self.duty_history.clear()
        self.start_timestamp_ms = None
        self.last_timestamp_ms = None

    def prepare_start(self, heater_id: int, duty_permille: int) -> None:
        if not 0 <= heater_id < HEATER_TEST_COUNT:
            raise ValueError("heater ID must be between 0 and 11")
        if not 0 <= duty_permille <= HEATER_TEST_MAX_DUTY:
            raise ValueError("duty must be between 0 and 20 permille")
        self.selected_heater = heater_id
        self.requested_duty = duty_permille
        self.state = CHARACTERIZATION_IDLE
        self.last_status_error = None
        self.reset_histories()

    def apply_command_response(self, response: CommandResponse) -> None:
        if not response.ok:
            self.last_status_error = f"command rejected: status {response.status}"
            return
        self.state = response.arg1
        if self.state in CHARACTERIZATION_STATE_NAMES:
            self.requested_duty = response.arg2
        self.last_status_error = None

    def apply_status_response(self, response: CommandResponse) -> None:
        if not response.ok:
            self.last_status_error = f"status rejected: status {response.status}"
            return
        self.state = response.arg1
        if self.state in CHARACTERIZATION_STATE_NAMES:
            self.requested_duty = response.arg2
        self.last_status_error = None

    def append_packet(self, packet: TelemetryPacket) -> None:
        if self.selected_heater is None or not self.is_active:
            return
        if self.start_timestamp_ms is None:
            self.start_timestamp_ms = packet.timestamp
        self.last_timestamp_ms = packet.timestamp
        elapsed_s = (packet.timestamp - self.start_timestamp_ms) / 1000.0
        for sensor_id in range(min(HEATER_TEST_COUNT, len(packet.temperatures))):
            valid = packet.temperature_valid(sensor_id)
            self.validity_histories[sensor_id].append((elapsed_s, valid))
            temperature = packet.temperature_c(sensor_id)
            if temperature is not None:
                self.temperature_histories[sensor_id].append((elapsed_s, temperature))
        applied_duty = self.requested_duty if self.state == CHARACTERIZATION_HEATING else 0
        self.duty_history.append((elapsed_s, float(applied_duty)))

    @property
    def is_active(self) -> bool:
        return self.state in {
            CHARACTERIZATION_BASELINE,
            CHARACTERIZATION_HEATING,
            CHARACTERIZATION_COOLDOWN,
        }

    @property
    def state_name(self) -> str:
        return CHARACTERIZATION_STATE_NAMES.get(self.state, f"unknown ({self.state})")

    @property
    def elapsed_s(self) -> float | None:
        if self.start_timestamp_ms is None or self.last_timestamp_ms is None:
            return None
        return (self.last_timestamp_ms - self.start_timestamp_ms) / 1000.0
