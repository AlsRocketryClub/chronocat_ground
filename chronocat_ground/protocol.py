from __future__ import annotations

from dataclasses import dataclass
import struct


DEFAULT_DEVICE_HOST = "192.168.1.50"
DEFAULT_COMMAND_PORT = 5006
DEFAULT_TELEMETRY_PORT = 5005

TELEMETRY_MAGIC = b"CCTM"
TELEMETRY_VERSION_V1 = 1
TELEMETRY_VERSION_V2 = 2
TELEMETRY_VERSION_V3 = 3
TELEMETRY_VERSION = TELEMETRY_VERSION_V3
TELEMETRY_MESSAGE_TYPE = 1
TELEMETRY_PACKET_SIZE_V1 = 131
TELEMETRY_PACKET_SIZE_V2 = 165
TELEMETRY_PACKET_SIZE_V3 = 167
TELEMETRY_PACKET_SIZE = TELEMETRY_PACKET_SIZE_V3
TELEMETRY_TEMP_COUNT = 13
TELEMETRY_OS_ADC_COUNT = 12
TELEMETRY_GEIGER_COUNT_V2 = 2
AD7177_DEVICE_COUNT = 4
AD7177_CHANNEL_COUNT = 3

GEIGER_RECORD_STRUCT = struct.Struct(">BBHIdffIHHBB")

AD7177_STATUS_RDY = 1 << 7
AD7177_STATUS_ADC_ERROR = 1 << 6
AD7177_STATUS_CRC_ERROR = 1 << 5
AD7177_STATUS_REG_ERROR = 1 << 4
AD7177_STATUS_CHANNEL_MASK = 0x03

TELEMETRY_FLAG_ENABLED = 1 << 0
TELEMETRY_FLAG_TCP_LISTENING = 1 << 1

TELEMETRY_HEALTH_NAMES = {
    0: "ok",
    1: "tcp not listening",
    2: "temperature sensor error",
}

GEIGER_ERROR_NAMES = {
    0: "ok",
    1: "HV error",
    2: "GM counter error",
    4: "technological parameters range error",
    8: "technological parameters initialization error",
    16: "INFO-flash writing error",
    32: "history writing error",
    64: "calibration: no statistics reset",
    128: "calibration: no background deduction",
}

COMMAND_PACKET_SIZE = 5
RESPONSE_PACKET_SIZE = 6

COMMAND_PING = 0x01
COMMAND_TELEMETRY_SET = 0x02
COMMAND_TELEMETRY_STATUS = 0x03
COMMAND_GEIGER_RESET_ACCUMULATED_DOSE = 0x47
COMMAND_GEIGER_CLEAR_HISTORY = 0x4F
COMMAND_GEIGER_RESET_STATS = 0x58

COMMAND_HEATER_SET_TARGET = 0x10
COMMAND_HEATER_GET_TARGET = 0x11
COMMAND_HEATER_SET_KP = 0x12
COMMAND_HEATER_GET_KP = 0x13
COMMAND_HEATER_SET_KI = 0x14
COMMAND_HEATER_GET_KI = 0x15
COMMAND_HEATER_SET_KD = 0x16
COMMAND_HEATER_GET_KD = 0x17

VALUE_OFF = 0x00
VALUE_ON = 0x01

STATUS_OK = 0x00
STATUS_BAD_MAGIC = 0x01
STATUS_BAD_COMMAND = 0x02
STATUS_BAD_VALUE = 0x03
STATUS_WRITE_FAILED = 0x04
STATUS_GEIGER_FAILED = 0x05


COMMAND_NAMES = {
    COMMAND_PING: "ping",
    COMMAND_TELEMETRY_SET: "telemetry set",
    COMMAND_TELEMETRY_STATUS: "telemetry status",
    COMMAND_GEIGER_RESET_ACCUMULATED_DOSE: "geiger reset accumulated dose",
    COMMAND_GEIGER_CLEAR_HISTORY: "geiger clear history",
    COMMAND_GEIGER_RESET_STATS: "geiger reset statistics",
    COMMAND_HEATER_SET_TARGET: "heater set target",
    COMMAND_HEATER_GET_TARGET: "heater get target",
    COMMAND_HEATER_SET_KP: "heater set Kp",
    COMMAND_HEATER_GET_KP: "heater get Kp",
    COMMAND_HEATER_SET_KI: "heater set Ki",
    COMMAND_HEATER_GET_KI: "heater get Ki",
    COMMAND_HEATER_SET_KD: "heater set Kd",
    COMMAND_HEATER_GET_KD: "heater get Kd",
}

STATUS_NAMES = {
    STATUS_OK: "ok",
    STATUS_BAD_MAGIC: "bad magic",
    STATUS_BAD_COMMAND: "bad command",
    STATUS_BAD_VALUE: "bad value",
    STATUS_WRITE_FAILED: "write failed",
    STATUS_GEIGER_FAILED: "geiger failed",
}


@dataclass(frozen=True)
class Ad7177Reading:
    slot: int
    adc_index: int
    channel_index: int
    word: int
    raw24: int
    status: int

    @property
    def rdy(self) -> bool:
        return (self.status & AD7177_STATUS_RDY) != 0

    @property
    def adc_error(self) -> bool:
        return (self.status & AD7177_STATUS_ADC_ERROR) != 0

    @property
    def crc_error(self) -> bool:
        return (self.status & AD7177_STATUS_CRC_ERROR) != 0

    @property
    def reg_error(self) -> bool:
        return (self.status & AD7177_STATUS_REG_ERROR) != 0

    @property
    def status_channel(self) -> int:
        return self.status & AD7177_STATUS_CHANNEL_MASK

    @property
    def has_error(self) -> bool:
        return self.adc_error or self.crc_error or self.reg_error


@dataclass(frozen=True)
class GeigerReading:
    valid: int
    counter_id: int
    error_flags: int
    event_id: int
    dose_cps: float
    dose_rate_cps: float
    total_dose_sv: float
    dose_time_sec: int
    stats_time_sec: int
    hv_voltage: int
    stat_error_percent: int
    stat_cell_count: int

    @classmethod
    def unavailable(cls, counter_id: int) -> GeigerReading:
        return cls(0, counter_id, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0)


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
    heater_duty_permille: int
    os_adc_valid_mask: int
    os_adc_readings: tuple[int, ...]
    geiger_readings: tuple[GeigerReading, ...]

    def geiger_reading(self, counter_id: int) -> GeigerReading | None:
        return next(
            (reading for reading in self.geiger_readings if reading.counter_id == counter_id),
            None,
        )

    @property
    def primary_geiger(self) -> GeigerReading:
        return self.geiger_reading(0) or GeigerReading.unavailable(0)

    @property
    def geiger_valid(self) -> int:
        return self.primary_geiger.valid

    @property
    def geiger_error_flags(self) -> int:
        return self.primary_geiger.error_flags

    @property
    def geiger_event_id(self) -> int:
        return self.primary_geiger.event_id

    @property
    def geiger_dose_cps(self) -> float:
        return self.primary_geiger.dose_cps

    @property
    def geiger_dose_rate_cps(self) -> float:
        return self.primary_geiger.dose_rate_cps

    @property
    def geiger_total_dose_sv(self) -> float:
        return self.primary_geiger.total_dose_sv

    @property
    def geiger_dose_time_sec(self) -> int:
        return self.primary_geiger.dose_time_sec

    @property
    def geiger_stats_time_sec(self) -> int:
        return self.primary_geiger.stats_time_sec

    @property
    def geiger_hv_voltage(self) -> int:
        return self.primary_geiger.hv_voltage

    @property
    def geiger_stat_error_percent(self) -> int:
        return self.primary_geiger.stat_error_percent

    @property
    def geiger_stat_cell_count(self) -> int:
        return self.primary_geiger.stat_cell_count

    @property
    def tcp_status(self) -> int:
        return 4 if (self.flags & TELEMETRY_FLAG_TCP_LISTENING) else 0

    @property
    def seq(self) -> int:
        return self.counter

    @property
    def tick_10ms(self) -> int:
        return self.timestamp

    @property
    def tick_ms(self) -> int:
        return self.timestamp

    @property
    def ad7177_readings(self) -> tuple[Ad7177Reading, ...]:
        return tuple(
            Ad7177Reading(
                slot=index,
                adc_index=index // AD7177_CHANNEL_COUNT,
                channel_index=index % AD7177_CHANNEL_COUNT,
                word=word,
                raw24=(word >> 8) & 0x00FFFFFF,
                status=word & 0xFF,
            )
            for index, word in enumerate(self.os_adc_readings)
        )

    def ad7177_reading(self, index: int) -> Ad7177Reading:
        return self.ad7177_readings[index]

    def temperature_valid(self, index: int) -> bool:
        return (self.temperature_valid_mask & (1 << index)) != 0

    def temperature_c(self, index: int) -> float | None:
        if not self.temperature_valid(index):
            return None
        return self.temperatures[index] / 100.0

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


def _parse_geiger_reading(
    data: bytes, offset: int, counter_id_override: int | None = None
) -> tuple[GeigerReading, int]:
    values = GEIGER_RECORD_STRUCT.unpack_from(data, offset)
    reading = GeigerReading(
        valid=values[0],
        counter_id=values[1] if counter_id_override is None else counter_id_override,
        error_flags=values[2],
        event_id=values[3],
        dose_cps=values[4],
        dose_rate_cps=values[5],
        total_dose_sv=values[6],
        dose_time_sec=values[7],
        stats_time_sec=values[8],
        hv_voltage=values[9],
        stat_error_percent=values[10],
        stat_cell_count=values[11],
    )
    return reading, offset + GEIGER_RECORD_STRUCT.size


def parse_telemetry_packet(data: bytes) -> TelemetryPacket:
    if len(data) < 10:
        raise ValueError(f"telemetry packet too short: {len(data)} bytes")

    magic = data[:4]
    if magic != TELEMETRY_MAGIC:
        raise ValueError(f"bad telemetry magic {magic!r}")

    version = data[4]
    expected_sizes = {
        TELEMETRY_VERSION_V1: TELEMETRY_PACKET_SIZE_V1,
        TELEMETRY_VERSION_V2: TELEMETRY_PACKET_SIZE_V2,
        TELEMETRY_VERSION_V3: TELEMETRY_PACKET_SIZE_V3,
    }
    expected_size = expected_sizes.get(version)
    if expected_size is None:
        raise ValueError(f"unsupported telemetry version {version}")
    if len(data) != expected_size:
        raise ValueError(f"expected {expected_size} telemetry bytes for version {version}, got {len(data)}")

    message_type = data[5]
    if message_type != TELEMETRY_MESSAGE_TYPE:
        raise ValueError(f"unsupported telemetry message type {message_type}")

    payload_length = struct.unpack_from(">H", data, 8)[0]
    if payload_length != expected_size:
        raise ValueError(f"bad telemetry payload length {payload_length}")

    offset = 0
    offset += 4
    offset += 1
    offset += 1
    flags = struct.unpack_from(">H", data, offset)[0]
    offset += 2
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

    heater_duty_permille = 0
    if version >= TELEMETRY_VERSION_V3:
        heater_duty_permille = struct.unpack_from(">H", data, offset)[0]
        offset += 2

    os_adc_valid_mask = struct.unpack_from(">H", data, offset)[0]
    offset += 2
    os_adc_readings = struct.unpack_from(f">{TELEMETRY_OS_ADC_COUNT}I", data, offset)
    offset += TELEMETRY_OS_ADC_COUNT * 4

    geiger_readings: list[GeigerReading] = []
    if version == TELEMETRY_VERSION_V1:
        reading, offset = _parse_geiger_reading(data, offset, counter_id_override=0)
        geiger_readings.append(reading)
    else:
        for _ in range(TELEMETRY_GEIGER_COUNT_V2):
            reading, offset = _parse_geiger_reading(data, offset)
            geiger_readings.append(reading)

        counter_ids = [reading.counter_id for reading in geiger_readings]
        if any(counter_id not in (0, 1) for counter_id in counter_ids):
            raise ValueError(f"invalid Geiger counter IDs {counter_ids}")
        if len(set(counter_ids)) != len(counter_ids):
            raise ValueError(f"duplicate Geiger counter IDs {counter_ids}")

    if offset != expected_size:
        raise ValueError(f"internal parser size mismatch: consumed {offset} bytes")

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
        heater_duty_permille=heater_duty_permille,
        os_adc_valid_mask=os_adc_valid_mask,
        os_adc_readings=os_adc_readings,
        geiger_readings=tuple(geiger_readings),
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


def geiger_reset_actions_name(value: int) -> str:
    actions = []
    if value & 0x01:
        actions.append("reset accumulated dose")
    if value & 0x02:
        actions.append("clear history")
    if value & 0x04:
        actions.append("reset statistics")
    if actions:
        return ", ".join(actions)
    return "none"


def geiger_error_names(error_flags: int) -> str:
    if not error_flags:
        return "ok"

    names = [
        name
        for mask, name in GEIGER_ERROR_NAMES.items()
        if mask and (error_flags & mask)
    ]
    known_mask = sum(mask for mask in GEIGER_ERROR_NAMES if mask)
    unknown = error_flags & ~known_mask
    if unknown:
        names.append(f"unknown bits 0x{unknown:04x}")
    return ", ".join(names)


def tcp_status_name(status: int) -> str:
    return TCP_STATUS_NAMES.get(status, f"0x{status:02x}")


def telemetry_health_name(health_code: int) -> str:
    return TELEMETRY_HEALTH_NAMES.get(health_code, f"0x{health_code:02x}")


def ad7177_status_names(status: int) -> str:
    names = []
    if status & AD7177_STATUS_RDY:
        names.append("RDY")
    if status & AD7177_STATUS_ADC_ERROR:
        names.append("ADC_ERROR")
    if status & AD7177_STATUS_CRC_ERROR:
        names.append("CRC_ERROR")
    if status & AD7177_STATUS_REG_ERROR:
        names.append("REG_ERROR")
    names.append(f"CH{status & AD7177_STATUS_CHANNEL_MASK}")
    return ", ".join(names)


HEATER_GAIN_SCALE = 1000.0


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
