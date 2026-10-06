from .protocol_constants import (
    AD7177_STATUS_ADC_ERROR,
    AD7177_STATUS_CHANNEL_MASK,
    AD7177_STATUS_CRC_ERROR,
    AD7177_STATUS_RDY,
    AD7177_STATUS_REG_ERROR,
    COMMAND_NAMES,
    GEIGER_ERROR_NAMES,
    GEIGER_FLAG_MASK,
    STATUS_NAMES,
    TCP_STATUS_NAMES,
    TELEMETRY_HEALTH_NAMES,
    VALUE_OFF,
    VALUE_ON,
)


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
    """Names of the flags set in the low byte; the high byte is a counter, not flags."""
    flags = error_flags & GEIGER_FLAG_MASK
    if not flags:
        return "ok"
    return ", ".join(name for mask, name in GEIGER_ERROR_NAMES.items() if flags & mask)


def geiger_flag_counter(error_flags: int) -> int:
    """The counter the detector keeps in the high byte of its flag word."""
    return error_flags >> 8

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
