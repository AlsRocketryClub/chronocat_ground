from __future__ import annotations

from dataclasses import dataclass

from .heater_safety import HEATER_COUNT


@dataclass(frozen=True)
class PidGains:
    kp: float
    ki: float
    kd: float


@dataclass(frozen=True)
class PidProfile:
    name: str
    gains_by_heater: tuple[PidGains, ...]

    def gains_for(self, heater_id: int) -> PidGains:
        return self.gains_by_heater[heater_id]


_DEFAULT_GAINS = PidGains(kp=2.25, ki=0.051, kd=4.0)

# Keep profiles as data so individually tuned heater values can be added without
# changing the page or command workflow.
PID_PROFILES = (
    PidProfile(
        name="Default",
        gains_by_heater=(_DEFAULT_GAINS,) * HEATER_COUNT,
    ),
)


def profile_by_name(name: str) -> PidProfile:
    for profile in PID_PROFILES:
        if profile.name == name:
            return profile
    raise KeyError(name)
