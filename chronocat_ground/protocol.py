"""Compatibility facade for the Chronocat wire protocol."""

from .command_codec import (
    CommandResponse,
    build_command,
    decode_float32_args,
    decode_heater_gain,
    decode_heater_target_c,
    encode_heater_duty_permille,
    encode_heater_gain,
    encode_heater_target_c,
    parse_command_response,
)
from .protocol_constants import *
from .protocol_formatting import (
    ad7177_status_names,
    command_name,
    geiger_error_names,
    geiger_reset_actions_name,
    status_name,
    tcp_status_name,
    telemetry_health_name,
    telemetry_value_name,
)
from .protocol_models import (
    Ad7177Reading,
    CombinedTelemetryPacket,
    GeigerReading,
    HeaterPidReading,
    PidTelemetryPacket,
    TelemetryPacket,
    heater_pid_averages,
)
from .telemetry_codec import parse_telemetry_packet, parse_telemetry_packets


__all__ = [
    name
    for name in globals()
    if not name.startswith("_")
]
