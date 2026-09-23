from __future__ import annotations

import struct

from .protocol_constants import (
    COMBINED_TELEMETRY_MESSAGE_TYPE,
    COMBINED_TELEMETRY_PACKET_SIZE,
    GEIGER_RECORD_STRUCT,
    HEATER_SENSOR_IDS,
    PID_TELEMETRY_HEATER_COUNT,
    PID_TELEMETRY_MESSAGE_TYPE,
    PID_TELEMETRY_PACKET_SIZE,
    TELEMETRY_GEIGER_COUNT_V2,
    TELEMETRY_MAGIC,
    TELEMETRY_MESSAGE_TYPE,
    TELEMETRY_OS_ADC_COUNT,
    TELEMETRY_PACKET_SIZE_V1,
    TELEMETRY_PACKET_SIZE_V2,
    TELEMETRY_PACKET_SIZE_V3,
    TELEMETRY_TEMP_COUNT,
    TELEMETRY_VERSION_V1,
    TELEMETRY_VERSION_V2,
    TELEMETRY_VERSION_V3,
)
from .protocol_models import (
    CombinedTelemetryPacket,
    GeigerReading,
    HeaterPidReading,
    PidTelemetryPacket,
    TelemetryPacket,
)


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


def _parse_pid_records(
    data: bytes, offset: int, expected_end: int,
    temperatures: tuple[int, ...] | None = None,
    temperature_valid_mask: int = 0,
) -> tuple[tuple[int, ...], tuple[HeaterPidReading, ...], int]:
    masks = struct.unpack_from(">HH", data, offset)
    offset += 4
    heaters: list[HeaterPidReading] = []
    for heater_id in range(PID_TELEMETRY_HEATER_COUNT):
        sensor_id = HEATER_SENSOR_IDS[heater_id]
        measurement_milli_c = (
            temperatures[sensor_id] * 10 if temperatures is not None else 0
        )
        target_milli_c = struct.unpack_from(">I", data, offset)[0]
        offset += 4
        duty_permille = struct.unpack_from(">H", data, offset)[0]
        offset += 2
        result = data[offset]
        offset += 1
        terms = struct.unpack_from(">fffffff", data, offset)
        offset += 28
        heaters.append(
            HeaterPidReading(
                sensor_id=sensor_id,
                sensor_valid=bool(temperature_valid_mask & (1 << sensor_id)),
                pid_enabled=bool(masks[0] & (1 << heater_id)),
                manual=bool(masks[1] & (1 << heater_id)),
                target_milli_c=target_milli_c,
                measurement_milli_c=measurement_milli_c,
                duty_permille=duty_permille,
                result=result,
                proportional_term=terms[0],
                integral_term=terms[1],
                derivative_term=terms[2],
                output=terms[3],
                kp=terms[4],
                ki=terms[5],
                kd=terms[6],
            )
        )

    if offset != expected_end:
        raise ValueError(f"PID telemetry size mismatch: consumed {offset} bytes")
    return masks, tuple(heaters), offset


def _parse_pid_telemetry_packet(data: bytes) -> PidTelemetryPacket:
    if len(data) != PID_TELEMETRY_PACKET_SIZE:
        raise ValueError(
            f"expected {PID_TELEMETRY_PACKET_SIZE} PID telemetry bytes, got {len(data)}"
        )
    if data[4] != TELEMETRY_VERSION_V3 or data[5] != PID_TELEMETRY_MESSAGE_TYPE:
        raise ValueError("unsupported PID telemetry header")

    payload_length = struct.unpack_from(">H", data, 8)[0]
    if payload_length != PID_TELEMETRY_PACKET_SIZE:
        raise ValueError(f"bad PID telemetry payload length {payload_length}")

    timestamp, counter = struct.unpack_from(">II", data, 10)
    masks, heaters, _offset = _parse_pid_records(
        data, 18, PID_TELEMETRY_PACKET_SIZE
    )
    return PidTelemetryPacket(
        version=data[4],
        message_type=data[5],
        flags=struct.unpack_from(">H", data, 6)[0],
        payload_length=payload_length,
        timestamp=timestamp,
        counter=counter,
        pid_enabled_mask=masks[0],
        manual_mask=masks[1],
        heaters=heaters,
    )


def _parse_combined_telemetry_packet(data: bytes) -> CombinedTelemetryPacket:
    if len(data) != COMBINED_TELEMETRY_PACKET_SIZE:
        raise ValueError(
            f"expected {COMBINED_TELEMETRY_PACKET_SIZE} combined telemetry bytes, "
            f"got {len(data)}"
        )
    if data[4] != TELEMETRY_VERSION_V3 or data[5] != COMBINED_TELEMETRY_MESSAGE_TYPE:
        raise ValueError("unsupported combined telemetry header")

    flags = struct.unpack_from(">H", data, 6)[0]
    payload_length = struct.unpack_from(">H", data, 8)[0]
    if payload_length != COMBINED_TELEMETRY_PACKET_SIZE:
        raise ValueError(f"bad combined telemetry payload length {payload_length}")
    timestamp, counter = struct.unpack_from(">II", data, 10)

    offset = 18
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

    geiger_readings: list[GeigerReading] = []
    for _ in range(TELEMETRY_GEIGER_COUNT_V2):
        reading, offset = _parse_geiger_reading(data, offset)
        geiger_readings.append(reading)
    counter_ids = [reading.counter_id for reading in geiger_readings]
    if any(counter_id not in (0, 1) for counter_id in counter_ids):
        raise ValueError(f"invalid Geiger counter IDs {counter_ids}")
    if len(set(counter_ids)) != len(counter_ids):
        raise ValueError(f"duplicate Geiger counter IDs {counter_ids}")

    standard = TelemetryPacket(
        version=TELEMETRY_VERSION_V3,
        message_type=COMBINED_TELEMETRY_MESSAGE_TYPE,
        flags=flags,
        payload_length=payload_length,
        timestamp=timestamp,
        counter=counter,
        health_code=health_code,
        temperature_valid_mask=temperature_valid_mask,
        temperatures=temperatures,
        heater_duty_permille=0,
        os_adc_valid_mask=os_adc_valid_mask,
        os_adc_readings=os_adc_readings,
        geiger_readings=tuple(geiger_readings),
    )
    masks, heaters, offset = _parse_pid_records(
        data, offset, COMBINED_TELEMETRY_PACKET_SIZE,
        temperatures, temperature_valid_mask,
    )
    pid = PidTelemetryPacket(
        version=TELEMETRY_VERSION_V3,
        message_type=COMBINED_TELEMETRY_MESSAGE_TYPE,
        flags=flags,
        payload_length=payload_length,
        timestamp=timestamp,
        counter=counter,
        pid_enabled_mask=masks[0],
        manual_mask=masks[1],
        heaters=heaters,
    )
    if offset != COMBINED_TELEMETRY_PACKET_SIZE:
        raise ValueError(f"combined telemetry size mismatch: consumed {offset} bytes")
    return CombinedTelemetryPacket(standard=standard, pid=pid)


def parse_telemetry_packet(
    data: bytes,
) -> TelemetryPacket | PidTelemetryPacket | CombinedTelemetryPacket:
    if len(data) < 10:
        raise ValueError(f"telemetry packet too short: {len(data)} bytes")

    magic = data[:4]
    if magic != TELEMETRY_MAGIC:
        raise ValueError(f"bad telemetry magic {magic!r}")

    version = data[4]
    if (version == TELEMETRY_VERSION_V3) and (data[5] == PID_TELEMETRY_MESSAGE_TYPE):
        return _parse_pid_telemetry_packet(data)
    if (version == TELEMETRY_VERSION_V3) and (
        data[5] == COMBINED_TELEMETRY_MESSAGE_TYPE
    ):
        return _parse_combined_telemetry_packet(data)
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


def parse_telemetry_packets(
    data: bytes,
) -> list[TelemetryPacket | PidTelemetryPacket | CombinedTelemetryPacket]:
    return [parse_telemetry_packet(data)]
