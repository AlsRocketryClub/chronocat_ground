"""Compatibility facade for CSV telemetry schema and logging."""

from datetime import datetime
import os
from uuid import uuid4

from .csv_logger import TelemetryCsvLogger, default_output_path, system_boot_id
from .csv_schema import (
    CSV_MODE_FULL,
    CSV_MODE_GEIGER_ONLY,
    GEIGER_CSV_FIELDS,
    GEIGER_ONLY_CSV_FIELDS,
    add_geiger_reading_to_row,
    combined_packet_to_row,
    csv_fieldnames,
    format_source,
    normalize_source,
    packet_to_geiger_rows,
    packet_to_row,
    pid_csv_fieldnames,
    pid_packet_to_row,
)

__all__ = [
    "CSV_MODE_FULL",
    "CSV_MODE_GEIGER_ONLY",
    "GEIGER_CSV_FIELDS",
    "GEIGER_ONLY_CSV_FIELDS",
    "TelemetryCsvLogger",
    "add_geiger_reading_to_row",
    "combined_packet_to_row",
    "csv_fieldnames",
    "format_source",
    "default_output_path",
    "normalize_source",
    "packet_to_geiger_rows",
    "packet_to_row",
    "pid_csv_fieldnames",
    "pid_packet_to_row",
    "system_boot_id",
]
