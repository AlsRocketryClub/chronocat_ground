from __future__ import annotations

from dataclasses import dataclass
import struct


DEFAULT_DEVICE_HOST = "192.168.1.50"
DEFAULT_COMMAND_PORT = 5006
DEFAULT_TELEMETRY_PORT = 5005

TELEMETRY_MAGIC = b"CCTM"
TELEMETRY_VERSION = 1
TELEMETRY_MESSAGE_TYPE = 1
TELEMETRY_PACKET_SIZE = 101
TELEMETRY_TEMP_COUNT = 13
TELEMETRY_OS_ADC_COUNT = 12

TELEMETRY_FLAG_ENABLED = 1 << 0
TELEMETRY_FLAG_TCP_LISTENING = 1 << 1

TELEMETRY_HEALTH_NAMES = {
    0: "ok",
    1: "tcp not listening",
}

COMMAND_PACKET_SIZE = 5
RESPONSE_PACKET_SIZE = 6

COMMAND_PING = 0x01
COMMAND_TELEMETRY_SET = 0x02
COMMAND_TELEMETRY_STATUS = 0x03

VALUE_OFF = 0x00
VALUE_ON = 0x01

STATUS_OK = 0x00
STATUS_BAD_MAGIC = 0x01
STATUS_BAD_COMMAND = 0x02
STATUS_BAD_VALUE = 0x03
STATUS_WRITE_FAILED = 0x04


COMMAND_NAMES = {
    COMMAND_PING: "ping",
    COMMAND_TELEMETRY_SET: "telemetry set",
    COMMAND_TELEMETRY_STATUS: "telemetry status",
}

STATUS_NAMES = {
    STATUS_OK: "ok",
    STATUS_BAD_MAGIC: "bad magic",
    STATUS_BAD_COMMAND: "bad command",
    STATUS_BAD_VALUE: "bad value",
    STATUS_WRITE_FAILED: "write failed",
}


@dataclass(frozen=True)
class TelemetryPacket:
    version: int
    message_type: int
    flags: int
    payload_length: int
    timestamp: int
    counter: int
    health_code: int
    temperature_valid_mask: int
    temperatures: tuple[int, ...]
    os_adc_valid_mask: int
    os_adc_readings: tuple[int, ...]
    cots_dosimeter: int

    @property
    def tcp_status(self) -> int:
        return 4 if (self.flags & TELEMETRY_FLAG_TCP_LISTENING) else 0

    @property
    def seq(self) -> int:
        return self.counter

    @property
    def tick_10ms(self) -> int:
        return self.timestamp

    def temperature_valid(self, index: int) -> bool:
        return (self.temperature_valid_mask & (1 << index)) != 0

    def os_adc_valid(self, index: int) -> bool:
        return (self.os_adc_valid_mask & (1 << index)) != 0


TCP_STATUS_NAMES = {
    0: "uninit",
    1: "tcp_new failed",
    2: "bind failed",
    3: "listen failed",
    4: "listening",
}


@dataclass(frozen=True)
class CommandResponse:
    status: int
    command: int
    arg1: int
    arg2: int

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def parse_telemetry_packet(data: bytes) -> TelemetryPacket:
    if len(data) != TELEMETRY_PACKET_SIZE:
        raise ValueError(f"expected {TELEMETRY_PACKET_SIZE} telemetry bytes, got {len(data)}")

    offset = 0
    magic = data[offset : offset + 4]
    offset += 4
    if magic != TELEMETRY_MAGIC:
        raise ValueError(f"bad telemetry magic {magic!r}")

    version = data[offset]
    offset += 1
    message_type = data[offset]
    offset += 1
    flags = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    payload_length = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    timestamp = struct.unpack_from(">I", data, offset)[0]
    offset += 4
    counter = struct.unpack_from(">I", data, offset)[0]
    offset += 4

    health_code = data[offset]
    offset += 1
    temperature_valid_mask = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    temperatures = struct.unpack_from(f">{TELEMETRY_TEMP_COUNT}h", data, offset)
    offset += TELEMETRY_TEMP_COUNT * 2
    os_adc_valid_mask = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    os_adc_readings = struct.unpack_from(f">{TELEMETRY_OS_ADC_COUNT}I", data, offset)
    offset += TELEMETRY_OS_ADC_COUNT * 4
    cots_dosimeter = struct.unpack_from(">I", data, offset)[0]
    offset += 4

    if offset != TELEMETRY_PACKET_SIZE:
        raise ValueError(f"internal parser size mismatch: consumed {offset} bytes")
    if version != TELEMETRY_VERSION:
        raise ValueError(f"unsupported telemetry version {version}")
    if message_type != TELEMETRY_MESSAGE_TYPE:
        raise ValueError(f"unsupported telemetry message type {message_type}")
    if payload_length != TELEMETRY_PACKET_SIZE:
        raise ValueError(f"bad telemetry payload length {payload_length}")

    return TelemetryPacket(
        version=version,
        message_type=message_type,
        flags=flags,
        payload_length=payload_length,
        timestamp=timestamp,
        counter=counter,
        health_code=health_code,
        temperature_valid_mask=temperature_valid_mask,
        temperatures=temperatures,
        os_adc_valid_mask=os_adc_valid_mask,
        os_adc_readings=os_adc_readings,
        cots_dosimeter=cots_dosimeter,
    )


def parse_telemetry_packets(data: bytes) -> list[TelemetryPacket]:
    return [parse_telemetry_packet(data)]


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


def command_name(command: int) -> str:
    return COMMAND_NAMES.get(command, f"0x{command:02x}")


def status_name(status: int) -> str:
    return STATUS_NAMES.get(status, f"0x{status:02x}")


def telemetry_value_name(value: int) -> str:
    if value == VALUE_ON:
        return "on"
    if value == VALUE_OFF:
        return "off"
    return f"0x{value:02x}"


def tcp_status_name(status: int) -> str:
    return TCP_STATUS_NAMES.get(status, f"0x{status:02x}")


def telemetry_health_name(health_code: int) -> str:
    return TELEMETRY_HEALTH_NAMES.get(health_code, f"0x{health_code:02x}")
