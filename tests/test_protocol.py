from __future__ import annotations

from datetime import datetime
import csv
from pathlib import Path
import struct
import tempfile
import unittest

from chronocat_ground.protocol import (
    GEIGER_RECORD_STRUCT,
    TELEMETRY_PACKET_SIZE_V1,
    TELEMETRY_PACKET_SIZE_V2,
    geiger_error_names,
    parse_telemetry_packet,
)
from chronocat_ground.telemetry_cli import build_parser
from chronocat_ground.telemetry_csv import (
    CSV_MODE_GEIGER_ONLY,
    GEIGER_ONLY_CSV_FIELDS,
    TelemetryCsvLogger,
    csv_fieldnames,
    packet_to_geiger_rows,
    packet_to_row,
)


def geiger_record(counter_id: int, event_id: int, dose_rate: float) -> bytes:
    return GEIGER_RECORD_STRUCT.pack(
        1,
        counter_id,
        0x0012 + counter_id,
        event_id,
        12.5 + counter_id,
        dose_rate,
        0.25 + counter_id,
        1000 + counter_id,
        200 + counter_id,
        450 + counter_id,
        3 + counter_id,
        20 + counter_id,
    )


def telemetry_packet(version: int, records: list[bytes]) -> bytes:
    size = TELEMETRY_PACKET_SIZE_V1 if version == 1 else TELEMETRY_PACKET_SIZE_V2
    prefix = bytearray(b"CCTM")
    prefix.extend((version, 1))
    prefix.extend(struct.pack(">HHII", 0x0003, size, 1234, 99))
    prefix.append(0)
    prefix.extend(struct.pack(">H", 0x0003))
    prefix.extend(struct.pack(">13h", *range(-6, 7)))
    prefix.extend(struct.pack(">H", 0x0001))
    prefix.extend(struct.pack(">12I", *range(12)))
    return bytes(prefix) + b"".join(records)


class TelemetryProtocolTests(unittest.TestCase):
    def test_parses_v1_and_preserves_legacy_properties(self) -> None:
        data = telemetry_packet(1, [geiger_record(0, 100, 1.5)])

        packet = parse_telemetry_packet(data)

        self.assertEqual(len(data), 131)
        self.assertEqual(packet.version, 1)
        self.assertEqual(len(packet.geiger_readings), 1)
        self.assertEqual(packet.geiger_reading(0).event_id, 100)
        self.assertIsNone(packet.geiger_reading(1))
        self.assertEqual(packet.geiger_event_id, 100)
        self.assertAlmostEqual(packet.geiger_dose_rate_cps, 1.5)
        self.assertEqual(packet.temperatures[0], -6)
        self.assertEqual(packet.os_adc_readings[-1], 11)

    def test_parses_two_v2_geiger_records_by_counter_id(self) -> None:
        data = telemetry_packet(
            2,
            [geiger_record(1, 201, 2.5), geiger_record(0, 200, 1.25)],
        )

        packet = parse_telemetry_packet(data)

        self.assertEqual(len(data), 165)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.geiger_reading(0).event_id, 200)
        self.assertEqual(packet.geiger_reading(1).event_id, 201)
        self.assertAlmostEqual(packet.geiger_reading(0).dose_rate_cps, 1.25)
        self.assertAlmostEqual(packet.geiger_reading(1).dose_rate_cps, 2.5)

    def test_rejects_wrong_size_and_declared_length(self) -> None:
        data = telemetry_packet(2, [geiger_record(0, 1, 1.0), geiger_record(1, 2, 2.0)])
        with self.assertRaisesRegex(ValueError, "expected 165"):
            parse_telemetry_packet(data[:-1])

        bad_length = bytearray(data)
        struct.pack_into(">H", bad_length, 8, 131)
        with self.assertRaisesRegex(ValueError, "bad telemetry payload length"):
            parse_telemetry_packet(bytes(bad_length))

    def test_rejects_bad_magic_and_unsupported_version(self) -> None:
        data = bytearray(
            telemetry_packet(1, [geiger_record(0, 100, 1.5)])
        )
        data[:4] = b"NOPE"
        with self.assertRaisesRegex(ValueError, "bad telemetry magic"):
            parse_telemetry_packet(bytes(data))

        data[:4] = b"CCTM"
        data[4] = 3
        with self.assertRaisesRegex(ValueError, "unsupported telemetry version 3"):
            parse_telemetry_packet(bytes(data))

    def test_rejects_duplicate_or_invalid_v2_counter_ids(self) -> None:
        duplicate = telemetry_packet(
            2,
            [geiger_record(0, 1, 1.0), geiger_record(0, 2, 2.0)],
        )
        with self.assertRaisesRegex(ValueError, "duplicate Geiger counter IDs"):
            parse_telemetry_packet(duplicate)

        invalid = telemetry_packet(
            2,
            [geiger_record(0, 1, 1.0), geiger_record(2, 2, 2.0)],
        )
        with self.assertRaisesRegex(ValueError, "invalid Geiger counter IDs"):
            parse_telemetry_packet(invalid)

    def test_csv_keeps_legacy_fields_and_adds_both_counters(self) -> None:
        packet = parse_telemetry_packet(
            telemetry_packet(
                2,
                [geiger_record(0, 300, 3.0), geiger_record(1, 301, 4.0)],
            )
        )

        fields = csv_fieldnames()
        row = packet_to_row(packet, datetime(2026, 1, 1), ("127.0.0.1", 5005))

        self.assertIn("geiger_event_id", fields)
        self.assertIn("geiger_0_event_id", fields)
        self.assertIn("geiger_1_event_id", fields)
        self.assertEqual(row["geiger_event_id"], 300)
        self.assertEqual(row["geiger_0_event_id"], 300)
        self.assertEqual(row["geiger_1_event_id"], 301)

    def test_v1_csv_leaves_second_counter_columns_empty(self) -> None:
        packet = parse_telemetry_packet(
            telemetry_packet(1, [geiger_record(0, 400, 5.0)])
        )

        row = packet_to_row(packet, datetime(2026, 1, 1), ("127.0.0.1", 5005))

        self.assertEqual(row["geiger_event_id"], 400)
        self.assertEqual(row["geiger_0_event_id"], 400)
        self.assertEqual(row["geiger_1_event_id"], "")

    def test_geiger_only_rows_contain_only_valid_detector_data(self) -> None:
        valid = geiger_record(0, 500, 6.5)
        invalid = bytes((0,)) + geiger_record(1, 501, 7.5)[1:]
        packet = parse_telemetry_packet(telemetry_packet(2, [valid, invalid]))

        rows = packet_to_geiger_rows(packet, datetime(2026, 1, 1))

        self.assertEqual(len(rows), 1)
        self.assertEqual(list(rows[0]), GEIGER_ONLY_CSV_FIELDS)
        self.assertEqual(rows[0]["counter_id"], 0)
        self.assertEqual(rows[0]["event_id"], 500)
        self.assertNotIn("error_names", rows[0])
        self.assertFalse(any(value == "" for value in rows[0].values()))

    def test_geiger_only_logger_writes_one_row_per_valid_counter(self) -> None:
        packet = parse_telemetry_packet(
            telemetry_packet(
                2,
                [geiger_record(0, 600, 8.5), geiger_record(1, 601, 9.5)],
            )
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geiger.csv"
            logger = TelemetryCsvLogger(path, mode=CSV_MODE_GEIGER_ONLY)
            logger.start()
            logger.write_packet(packet, ("127.0.0.1", 5005), datetime(2026, 1, 1))
            logger.write_packet(packet, ("127.0.0.1", 5005), datetime(2026, 1, 1, 0, 0, 1))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual([int(row["counter_id"]) for row in rows], [0, 1])
        self.assertEqual(list(rows[0]), GEIGER_ONLY_CSV_FIELDS)

    def test_geiger_only_logger_keeps_changed_sample_with_same_times(self) -> None:
        first = parse_telemetry_packet(
            telemetry_packet(1, [geiger_record(0, 700, 10.5)])
        )
        changed = parse_telemetry_packet(
            telemetry_packet(1, [geiger_record(0, 700, 11.5)])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geiger.csv"
            logger = TelemetryCsvLogger(path, mode=CSV_MODE_GEIGER_ONLY)
            logger.start()
            logger.write_packet(first, ("127.0.0.1", 5005), datetime(2026, 1, 1))
            logger.write_packet(
                changed, ("127.0.0.1", 5005), datetime(2026, 1, 1, 0, 0, 1)
            )
            logger.stop()

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual([float(row["dose_rate_cps"]) for row in rows], [10.5, 11.5])

    def test_geiger_only_logger_restart_resets_deduplication(self) -> None:
        packet = parse_telemetry_packet(
            telemetry_packet(1, [geiger_record(0, 800, 12.5)])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geiger.csv"
            logger = TelemetryCsvLogger(path, mode=CSV_MODE_GEIGER_ONLY)
            logger.start()
            logger.write_packet(packet, ("127.0.0.1", 5005))
            logger.stop()
            logger.start()
            logger.write_packet(packet, ("127.0.0.1", 5005))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)

    def test_geiger_error_names_match_detector_documentation(self) -> None:
        self.assertEqual(geiger_error_names(0x0002), "GM counter error")
        self.assertEqual(geiger_error_names(0x0020), "history writing error")
        self.assertIn("calibration: no statistics reset", geiger_error_names(0x0040))
        self.assertIn("unknown bits 0x0100", geiger_error_names(0x0100))

    def test_cli_accepts_geiger_only(self) -> None:
        args = build_parser().parse_args(["--geiger-only"])
        self.assertTrue(args.geiger_only)


if __name__ == "__main__":
    unittest.main()
