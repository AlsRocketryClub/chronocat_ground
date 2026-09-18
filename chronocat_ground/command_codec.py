from __future__ import annotations

from dataclasses import dataclass
import math
import struct

from .protocol_constants import (
    HEATER_GAIN_SCALE,
    HEATER_MANUAL_MAX_DUTY_PERMILLE,
    RESPONSE_PACKET_SIZE,
    STATUS_OK,
)


@dataclass(frozen=True)
class CommandResponse:
    status: int
    command: int
    arg1: int
    arg2: int

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def build_command(command: int, arg1: int = 0, arg2: int = 0) -> bytes:
    if not 0 <= command <= 0xFF:
        raise ValueError("command must fit in one byte")
    if not 0 <= arg1 <= 0xFFFF:
        raise ValueError("arg1 must fit in two bytes")
    if not 0 <= arg2 <= 0xFFFF:
        raise ValueError("arg2 must fit in two bytes")

    return bytes((command, (arg1 >> 8) & 0xFF, arg1 & 0xFF, (arg2 >> 8) & 0xFF, arg2 & 0xFF))


def parse_command_response(data: bytes) -> CommandResponse:
    if len(data) != RESPONSE_PACKET_SIZE:
        raise ValueError(f"expected {RESPONSE_PACKET_SIZE} response bytes, got {len(data)}")

    arg1 = (data[2] << 8) | data[3]
    arg2 = (data[4] << 8) | data[5]
    return CommandResponse(status=data[0], command=data[1], arg1=arg1, arg2=arg2)


def decode_float32_args(arg1: int, arg2: int) -> float:
    """Decode a float whose 32 wire bits are returned as two big-endian words."""
    if not 0 <= arg1 <= 0xFFFF or not 0 <= arg2 <= 0xFFFF:
        raise ValueError("float words must fit in two bytes")
    value = struct.unpack(">f", struct.pack(">HH", arg1, arg2))[0]
    if not math.isfinite(value):
        raise ValueError("float response is not finite")
    return value


def encode_heater_target_c(value: float) -> int:
    if not (0 <= value < 65.0):
        raise ValueError(f"heater target {value} out of range 0..64.999 C")
    return int(value * 1000.0 + 0.5)


def decode_heater_target_c(encoded: int) -> float:
    return encoded / 1000.0


def encode_heater_gain(value: float) -> int:
    if not (0.0 <= value <= 65.535):
        raise ValueError(f"heater gain {value} out of range 0..65.535")
    return int(value * HEATER_GAIN_SCALE + 0.5)


def decode_heater_gain(encoded: int) -> float:
    return encoded / HEATER_GAIN_SCALE


def encode_heater_duty_permille(value: int) -> int:
    if not (0 <= value <= HEATER_MANUAL_MAX_DUTY_PERMILLE):
        raise ValueError(
            f"heater duty {value} out of range "
            f"0..{HEATER_MANUAL_MAX_DUTY_PERMILLE} permille"
        )
    return value
