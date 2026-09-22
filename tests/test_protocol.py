from __future__ import annotations

from datetime import datetime
import csv
import errno
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from chronocat_ground.protocol import (
    GEIGER_RECORD_STRUCT,
    COMBINED_TELEMETRY_PACKET_SIZE,
    PID_TELEMETRY_PACKET_SIZE,
    PID_TELEMETRY_RECORD_SIZE,
    TELEMETRY_PACKET_SIZE_V1,
    TELEMETRY_PACKET_SIZE_V2,
    TELEMETRY_TEMP_COUNT,
    encode_heater_gain,
    encode_heater_target_c,
    encode_heater_duty_permille,
    heater_pid_averages,
    decode_heater_gain,
    decode_heater_target_c,
    decode_float32_args,
    geiger_error_names,
    parse_telemetry_packet,
    telemetry_health_name,
    build_command,
    parse_command_response,
    PID_FLAG_RESERVED_3,
    PID_RESULT_NAMES,
    COMMAND_HEATER_SET_TARGET,
    COMMAND_HEATER_SET_KP,
    COMMAND_HEATER_SET_KI,
    COMMAND_HEATER_SET_KD,
    COMMAND_HEATER_SET_MANUAL_DUTY,
    COMMAND_HEATER_RETURN_TO_PID,
    COMMAND_HEATER_ALL_ON,
    COMMAND_HEATER_ALL_OFF,
    COMMAND_GEIGER_READ_XDER,
)
from chronocat_ground.telemetry_cli import build_parser
from chronocat_ground.heater_safety import HEATER_SENSOR_IDS
from chronocat_ground.telemetry_csv import (
    CSV_MODE_GEIGER_ONLY,
    GEIGER_ONLY_CSV_FIELDS,
    TelemetryCsvLogger,
    csv_fieldnames,
    default_output_path,
    packet_to_geiger_rows,
    packet_to_row,
    combined_packet_to_row,
    pid_csv_fieldnames,
    pid_packet_to_row,
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


def telemetry_packet(
    version: int,
    records: list[bytes],
    adc_words: tuple[int, ...] | None = None,
    adc_valid_mask: int = 0x0001,
) -> bytes:
    size = TELEMETRY_PACKET_SIZE_V1 if version == 1 else TELEMETRY_PACKET_SIZE_V2
    prefix = bytearray(b"CCTM")
    prefix.extend((version, 1))
    prefix.extend(struct.pack(">HHII", 0x0003, size, 1234, 99))
    prefix.append(0)
    prefix.extend(struct.pack(">H", 0x0003))
    prefix.extend(
        struct.pack(f">{TELEMETRY_TEMP_COUNT}h", *range(-6, -6 + TELEMETRY_TEMP_COUNT))
    )
    prefix.extend(struct.pack(">H", adc_valid_mask))
    prefix.extend(
        struct.pack(">12I", *(range(12) if adc_words is None else adc_words))
    )
    return bytes(prefix) + b"".join(records)


def pid_telemetry_packet() -> bytes:
    packet = bytearray(PID_TELEMETRY_PACKET_SIZE)
    packet[:6] = b"CCTM\x03\x02"
    struct.pack_into(">HHII", packet, 6, 0x0003, PID_TELEMETRY_PACKET_SIZE, 1234, 99)
    packet[18:20] = bytes((12, PID_TELEMETRY_RECORD_SIZE))
    struct.pack_into(">HHHHHH", packet, 20, 0x003F, 0x0001, 0x0001, 0, 0x0001, 0)
    offset = 32
    for heater_id in range(12):
        struct.pack_into(">BBH", packet, offset, heater_id, 3 if heater_id == 0 else 0xFF, 0x0061 if heater_id == 0 else 0)
        struct.pack_into(
            ">IiHBBfffffff",
            packet,
            offset + 4,
            20000,
            21500,
            42,
            1 if heater_id == 0 else 0,
            0,
            15.0,
            2.0,
            0.5,
            17.5,
            10.0,
            0.1,
            0.0,
        )
        offset += PID_TELEMETRY_RECORD_SIZE
    return bytes(packet)


def combined_telemetry_packet(
    adc_words: tuple[int, ...] | None = None,
    adc_valid_mask: int = 0x0001,
) -> bytes:
    standard = telemetry_packet(
        2,
        [geiger_record(0, 300, 3.0), geiger_record(1, 301, 4.0)],
        adc_words,
        adc_valid_mask,
    )
    pid = pid_telemetry_packet()
    packet = bytearray()
    packet.extend(b"CCTM\x03\x03")
    packet.extend(struct.pack(">HHII", 0x0007, COMBINED_TELEMETRY_PACKET_SIZE, 1234, 99))
    packet.extend(standard[18:])
    packet.extend(pid[18:])
    assert len(packet) == COMBINED_TELEMETRY_PACKET_SIZE
    return bytes(packet)


class TelemetryProtocolTests(unittest.TestCase):
    def test_decodes_xder_float_from_reply_words(self) -> None:
        bits = struct.unpack(">I", struct.pack(">f", 1.25))[0]
        value = decode_float32_args(bits >> 16, bits & 0xFFFF)

        self.assertAlmostEqual(value, 1.25)
        self.assertEqual(COMMAND_GEIGER_READ_XDER, 0x50)
        self.assertEqual(build_command(COMMAND_GEIGER_READ_XDER, 1, 0), b"\x50\x00\x01\x00\x00")

    def test_rejects_non_finite_xder_float(self) -> None:
        with self.assertRaises(ValueError):
            decode_float32_args(0x7F80, 0x0000)

    def test_heater_temperature_mapping_uses_both_boards(self) -> None:
        self.assertEqual(HEATER_SENSOR_IDS[:6], (3, 4, 5, 0, 1, 2))
        self.assertEqual(HEATER_SENSOR_IDS[6:], (9, 10, 11, 6, 7, 8))

    def test_parses_combined_telemetry_packet(self) -> None:
        packet = parse_telemetry_packet(combined_telemetry_packet())

        self.assertEqual(packet.standard.timestamp, 1234)
        self.assertEqual(packet.pid.counter, 99)
        self.assertEqual(packet.pid.heaters[0].duty_permille, 42)
        self.assertEqual(packet.standard.geiger_reading(1).event_id, 301)

    def test_parses_all_adc_slots_and_validity_bits(self) -> None:
        words = tuple(((0x120000 + slot) << 8) | (slot % 3) for slot in range(12))
        packet = parse_telemetry_packet(
            combined_telemetry_packet(words, 0x0FFF)
        ).standard

        self.assertEqual(packet.os_adc_valid_mask, 0x0FFF)
        self.assertEqual(len(packet.ad7177_readings), 12)
        for slot, reading in enumerate(packet.ad7177_readings):
            self.assertEqual(reading.slot, slot)
            self.assertEqual(reading.adc_index, slot // 3)
            self.assertEqual(reading.channel_index, slot % 3)
            self.assertEqual(reading.raw24, 0x120000 + slot)
            self.assertEqual(reading.status_channel, slot % 3)
            self.assertTrue(packet.os_adc_valid(slot))

    def test_parses_pid_telemetry_for_all_heaters(self) -> None:
        packet = parse_telemetry_packet(pid_telemetry_packet())

        self.assertEqual(len(packet.heaters), 12)
        self.assertEqual(packet.heaters[0].sensor_id, 3)
        self.assertTrue(packet.heaters[0].sensor_valid)
        self.assertAlmostEqual(packet.heaters[0].kp, 10.0)
        self.assertEqual(packet.heaters[1].sensor_id, 0xFF)
        self.assertEqual(packet.heaters[1].result_name, "disabled")

    def test_removed_characterization_values_remain_reserved(self) -> None:
        self.assertEqual(PID_FLAG_RESERVED_3, 1 << 3)
        self.assertEqual(PID_RESULT_NAMES[4], "reserved")
        self.assertEqual(PID_RESULT_NAMES[5], "unmapped sensor")

    def test_calculates_heater_temperature_and_duty_averages(self) -> None:
        packet = parse_telemetry_packet(pid_telemetry_packet())

        average_temperature, average_duty = heater_pid_averages(packet.heaters)

        self.assertAlmostEqual(average_temperature, 21.5)
        self.assertAlmostEqual(average_duty, 42.0)

    def test_parses_v1_and_preserves_legacy_properties(self) -> None:
        data = telemetry_packet(1, [geiger_record(0, 100, 1.5)])

        packet = parse_telemetry_packet(data)

        self.assertEqual(len(data), TELEMETRY_PACKET_SIZE_V1)
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

        self.assertEqual(len(data), TELEMETRY_PACKET_SIZE_V2)
        self.assertEqual(packet.version, 2)
        self.assertEqual(packet.geiger_reading(0).event_id, 200)
        self.assertEqual(packet.geiger_reading(1).event_id, 201)
        self.assertAlmostEqual(packet.geiger_reading(0).dose_rate_cps, 1.25)
        self.assertAlmostEqual(packet.geiger_reading(1).dose_rate_cps, 2.5)

    def test_rejects_wrong_size_and_declared_length(self) -> None:
        data = telemetry_packet(2, [geiger_record(0, 1, 1.0), geiger_record(1, 2, 2.0)])
        with self.assertRaisesRegex(ValueError, f"expected {TELEMETRY_PACKET_SIZE_V2}"):
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
        data[4] = 4
        with self.assertRaisesRegex(ValueError, "unsupported telemetry version 4"):
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

    def test_pid_csv_contains_all_heater_records(self) -> None:
        packet = parse_telemetry_packet(pid_telemetry_packet())

        fields = pid_csv_fieldnames()
        row = pid_packet_to_row(packet, datetime(2026, 1, 1), ("127.0.0.1", 5005))

        self.assertIn("heater_11_duty_permille", fields)
        self.assertEqual(row["heater_0_duty_permille"], 42)
        self.assertEqual(row["heater_11_duty_permille"], 42)

    def test_full_logger_merges_pid_into_one_csv_row(self) -> None:
        standard = parse_telemetry_packet(
            telemetry_packet(2, [geiger_record(0, 450, 4.5), geiger_record(1, 451, 5.5)])
        )
        pid = parse_telemetry_packet(pid_telemetry_packet())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.csv"
            logger = TelemetryCsvLogger(path, durable=False)
            logger.start()
            logger.write_packet(standard, ("127.0.0.1", 5005), datetime(2026, 1, 1))
            logger.write_packet(pid, ("127.0.0.1", 5005), datetime(2026, 1, 1))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["heater_0_duty_permille"], "42")
        self.assertEqual(rows[0]["heater_11_duty_permille"], "42")
        self.assertEqual(rows[0]["geiger_0_event_id"], "450")

    def test_full_logger_writes_combined_packet_directly(self) -> None:
        packet = parse_telemetry_packet(combined_telemetry_packet())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "combined.csv"
            logger = TelemetryCsvLogger(path, durable=False)
            logger.start()
            logger.write_packet(packet, ("127.0.0.1", 5005), datetime(2026, 1, 1))
            logger.stop()

            with path.open(newline="", encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["heater_11_duty_permille"], "42")
        self.assertEqual(rows[0]["counter"], "99")

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

    def test_logger_restart_refuses_to_truncate_existing_file(self) -> None:
        packet = parse_telemetry_packet(
            telemetry_packet(1, [geiger_record(0, 800, 12.5)])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geiger.csv"
            logger = TelemetryCsvLogger(path, mode=CSV_MODE_GEIGER_ONLY)
            logger.start()
            logger.write_packet(packet, ("127.0.0.1", 5005))
            logger.stop()
            original = path.read_bytes()

            with self.assertRaises(FileExistsError):
                logger.start()

            self.assertEqual(path.read_bytes(), original)

    def test_logger_requires_explicit_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.csv"
            path.write_text("valuable measurements\n", encoding="utf-8")

            with self.assertRaises(FileExistsError):
                TelemetryCsvLogger(path, durable=False).start()
            self.assertEqual(
                path.read_text(encoding="utf-8"), "valuable measurements\n"
            )

            logger = TelemetryCsvLogger(path, overwrite=True, durable=False)
            logger.start()
            logger.stop()
            self.assertNotIn("valuable measurements", path.read_text(encoding="utf-8"))

    def test_automatic_names_include_boot_and_unique_session_ids(self) -> None:
        class FakeUuid:
            def __init__(self, value: str) -> None:
                self.hex = value

        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            with patch(
                "chronocat_ground.telemetry_csv.datetime"
            ) as mocked_datetime, patch(
                "chronocat_ground.telemetry_csv.uuid4",
                side_effect=[FakeUuid("11111111"), FakeUuid("22222222")],
            ):
                mocked_datetime.now.return_value = datetime(2026, 8, 20, 17, 45, 9)
                first = default_output_path(output_dir, boot_id="deadbeef")
                second = default_output_path(output_dir, boot_id="deadbeef")

        self.assertEqual(first.name, "telemetry_20260820_174509_deadbeef_11111111.csv")
        self.assertEqual(second.name, "telemetry_20260820_174509_deadbeef_22222222.csv")
        self.assertNotEqual(first, second)

    def test_automatic_collision_selects_another_file(self) -> None:
        class FakeUuid:
            hex = "newsession"

        with tempfile.TemporaryDirectory() as directory:
            collision = Path(directory) / "telemetry_collision.csv"
            collision.write_text("do not replace\n", encoding="utf-8")
            logger = TelemetryCsvLogger(durable=False)
            logger.path = collision

            with patch(
                "chronocat_ground.telemetry_csv.uuid4", return_value=FakeUuid()
            ):
                logger.start()
            logger.stop()

            self.assertEqual(collision.read_text(encoding="utf-8"), "do not replace\n")
            self.assertNotEqual(logger.path, collision)
            self.assertTrue(logger.path.exists())

    def test_durable_logger_syncs_header_packet_and_close(self) -> None:
        packet = parse_telemetry_packet(
            telemetry_packet(1, [geiger_record(0, 900, 13.5)])
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "durable.csv"
            logger = TelemetryCsvLogger(path, mode=CSV_MODE_GEIGER_ONLY)
            with patch("chronocat_ground.telemetry_csv.os.fsync") as fsync:
                logger.start()
                logger.write_packet(packet, ("127.0.0.1", 5005))
                logger.stop()

        self.assertGreaterEqual(fsync.call_count, 3)

    def test_durable_logger_syncs_new_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new" / "nested" / "durable.csv"
            logger = TelemetryCsvLogger(path)
            with patch("chronocat_ground.telemetry_csv.os.fsync") as fsync:
                logger.start()
                logger.stop()

        self.assertGreaterEqual(fsync.call_count, 7)

    def test_durable_logger_propagates_storage_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "durable.csv"
            logger = TelemetryCsvLogger(path)
            error = OSError(errno.EIO, "simulated storage failure")
            with patch(
                "chronocat_ground.telemetry_csv.os.fsync",
                side_effect=[None, error],
            ):
                with self.assertRaises(OSError) as raised:
                    logger.start()

            self.assertEqual(raised.exception.errno, errno.EIO)
            self.assertFalse(logger.active)

    def test_geiger_error_names_match_detector_documentation(self) -> None:
        self.assertEqual(geiger_error_names(0x0002), "GM counter error")
        self.assertEqual(geiger_error_names(0x0020), "history writing error")
        self.assertIn("calibration: no statistics reset", geiger_error_names(0x0040))
        self.assertIn("unknown bits 0x0100", geiger_error_names(0x0100))

    def test_cli_accepts_geiger_only(self) -> None:
        args = build_parser().parse_args(["--geiger-only", "--overwrite", "--out", "test.csv"])
        self.assertTrue(args.geiger_only)
        self.assertTrue(args.overwrite)

    def test_v2_invalid_geiger_1_with_id_1_accepted(self) -> None:
        valid = geiger_record(0, 100, 1.5)
        invalid = GEIGER_RECORD_STRUCT.pack(
            0, 1, 0, 0, 0.0, 0.0, 0.0, 0, 0, 0, 0, 0,
        )
        data = telemetry_packet(2, [valid, invalid])
        packet = parse_telemetry_packet(data)

        self.assertEqual(len(data), TELEMETRY_PACKET_SIZE_V2)
        self.assertTrue(packet.geiger_reading(0).valid)
        self.assertFalse(packet.geiger_reading(1).valid)
        self.assertEqual(packet.geiger_reading(1).counter_id, 1)

    def test_health_name_temperature_sensor_error(self) -> None:
        self.assertEqual(telemetry_health_name(2), "temperature sensor error")

    def test_temperature_c_valid(self) -> None:
        data = bytearray(telemetry_packet(2, [geiger_record(0, 1, 1.0), geiger_record(1, 2, 2.0)]))
        struct.pack_into(">h", data, 21, 2345)
        struct.pack_into(">H", data, 19, 0x0001)
        packet = parse_telemetry_packet(bytes(data))

        self.assertAlmostEqual(packet.temperature_c(0), 23.45)
        self.assertEqual(packet.temperatures[0], 2345)

    def test_temperature_c_invalid(self) -> None:
        data = bytearray(telemetry_packet(2, [geiger_record(0, 1, 1.0), geiger_record(1, 2, 2.0)]))
        struct.pack_into(">H", data, 19, 0x0000)
        packet = parse_telemetry_packet(bytes(data))

        self.assertIsNone(packet.temperature_c(0))
        self.assertIsNone(packet.temperature_c(1))

    def test_temperature_c_negative(self) -> None:
        data = bytearray(telemetry_packet(2, [geiger_record(0, 1, 1.0), geiger_record(1, 2, 2.0)]))
        struct.pack_into(">h", data, 21, -625)
        struct.pack_into(">H", data, 19, 0x0001)
        packet = parse_telemetry_packet(bytes(data))

        self.assertAlmostEqual(packet.temperature_c(0), -6.25)

    def test_encode_heater_target_c(self) -> None:
        self.assertEqual(encode_heater_target_c(60.0), 60000)
        self.assertEqual(encode_heater_target_c(0.0), 0)
        self.assertEqual(encode_heater_target_c(64.99), 64990)
        with self.assertRaises(ValueError):
            encode_heater_target_c(-1.0)
        with self.assertRaises(ValueError):
            encode_heater_target_c(65.0)

    def test_decode_heater_target_c(self) -> None:
        self.assertAlmostEqual(decode_heater_target_c(60000), 60.0)
        self.assertAlmostEqual(decode_heater_target_c(0), 0.0)

    def test_encode_heater_gain(self) -> None:
        self.assertEqual(encode_heater_gain(10.0), 10000)
        self.assertEqual(encode_heater_gain(0.1), 100)
        self.assertEqual(encode_heater_gain(0.0), 0)
        with self.assertRaises(ValueError):
            encode_heater_gain(-1.0)

    def test_decode_heater_gain(self) -> None:
        self.assertAlmostEqual(decode_heater_gain(10000), 10.0)
        self.assertAlmostEqual(decode_heater_gain(100), 0.1)
        self.assertAlmostEqual(decode_heater_gain(0), 0.0)

    def test_heater_command_bytes(self) -> None:
        cmd = build_command(COMMAND_HEATER_SET_TARGET, 0, 60000)
        self.assertEqual(cmd, bytes([0x10, 0x00, 0x00, 0xEA, 0x60]))

        cmd = build_command(COMMAND_HEATER_SET_KP, 0, 10000)
        self.assertEqual(cmd, bytes([0x12, 0x00, 0x00, 0x27, 0x10]))

        cmd = build_command(COMMAND_HEATER_SET_KI, 0, 100)
        self.assertEqual(cmd, bytes([0x14, 0x00, 0x00, 0x00, 0x64]))

        cmd = build_command(COMMAND_HEATER_SET_KD, 0, 0)
        self.assertEqual(cmd, bytes([0x16, 0x00, 0x00, 0x00, 0x00]))

        cmd = build_command(COMMAND_HEATER_SET_MANUAL_DUTY, 0, 250)
        self.assertEqual(cmd, bytes([0x18, 0x00, 0x00, 0x00, 0xFA]))

        cmd = build_command(COMMAND_HEATER_RETURN_TO_PID, 0, 0)
        self.assertEqual(cmd, bytes([0x19, 0x00, 0x00, 0x00, 0x00]))

        cmd = build_command(COMMAND_HEATER_ALL_ON, 0, 250)
        self.assertEqual(cmd, bytes([0x1B, 0x00, 0x00, 0x00, 0xFA]))

        cmd = build_command(COMMAND_HEATER_ALL_OFF, 0, 0)
        self.assertEqual(cmd, bytes([0x1C, 0x00, 0x00, 0x00, 0x00]))

    def test_encode_heater_duty_permille(self) -> None:
        self.assertEqual(encode_heater_duty_permille(0), 0)
        self.assertEqual(encode_heater_duty_permille(250), 250)
        with self.assertRaises(ValueError):
            encode_heater_duty_permille(-1)
        with self.assertRaises(ValueError):
            encode_heater_duty_permille(251)

    def test_heater_response_parse(self) -> None:
        resp = parse_command_response(bytes([0x00, 0x10, 0x00, 0x00, 0xEA, 0x60]))
        self.assertEqual(resp.status, 0)
        self.assertEqual(resp.command, 0x10)
        self.assertEqual(resp.arg1, 0)
        self.assertEqual(resp.arg2, 60000)

        resp = parse_command_response(bytes([0x00, 0x1B, 0x00, 0x00, 0x00, 0xFA]))
        self.assertEqual(resp.command, COMMAND_HEATER_ALL_ON)
        self.assertEqual(resp.arg1, 0)
        self.assertEqual(resp.arg2, 250)


if __name__ == "__main__":
    unittest.main()
