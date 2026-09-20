from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import csv
from pathlib import Path
import tempfile
import unittest

from chronocat_ground.protocol import (
    AD7177_BIPOLAR_MIDSCALE,
    AD7177_VREF_VOLTS,
    GeigerReading,
    TelemetryPacket,
)
from chronocat_ground.heater_safety import all_heaters_safe
from chronocat_ground.telemetry_csv import (
    CSV_MODE_GEIGER_ONLY,
    TelemetryCsvLogger,
    packet_to_row,
)
from chronocat_ground.telemetry_db import TelemetryDb, archive_database
from chronocat_ground.telemetry_history import TelemetryHistory
from chronocat_ground.protocol_models import CombinedTelemetryPacket, PidTelemetryPacket


def sample_packet() -> TelemetryPacket:
    temperatures = (2500, 0, 3000) + (0,) * 10
    adc_words = ((10 << 8), 0, (30 << 8)) + (0,) * 9
    geiger = GeigerReading(
        valid=1,
        counter_id=0,
        error_flags=0,
        event_id=4,
        dose_cps=1.0,
        dose_rate_cps=2.0,
        total_dose_sv=3.0,
        dose_time_sec=5,
        stats_time_sec=6,
        hv_voltage=7,
        stat_error_percent=0,
        stat_cell_count=8,
    )
    return TelemetryPacket(
        version=3,
        message_type=1,
        flags=0,
        payload_length=167,
        timestamp=100,
        counter=2,
        health_code=0,
        temperature_valid_mask=(1 << 0) | (1 << 2),
        temperatures=temperatures,
        heater_duty_permille=0,
        os_adc_valid_mask=(1 << 0) | (1 << 2),
        os_adc_readings=adc_words,
        geiger_readings=(geiger,),
    )


class TelemetryHistoryTests(unittest.TestCase):
    def test_global_heater_interlock_requires_all_twelve_safe_sensors(self) -> None:
        self.assertFalse(all_heaters_safe(sample_packet()))

    def test_record_uses_one_snapshot_and_persists_all_valid_temperatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = TelemetryDb(Path(directory) / "telemetry.db")
            history = TelemetryHistory(database)

            snapshot = history.record(sample_packet(), 10.0, 20.0)

            self.assertEqual(snapshot.packet_count, 1)
            self.assertEqual(snapshot.geiger_points[0][-1], (10.0, 20.0, 2.0))
            expected_average_volts = (
                (20 - AD7177_BIPOLAR_MIDSCALE) / AD7177_BIPOLAR_MIDSCALE * AD7177_VREF_VOLTS
            )
            self.assertEqual(
                snapshot.adc_average_points[-1], (10.0, 20.0, expected_average_volts)
            )
            self.assertEqual(database.query_temperature(0), [(20.0, 25.0)])
            self.assertEqual(database.query_temperature(2), [(20.0, 30.0)])
            self.assertEqual(database.query_temperature(1), [])
            database.close()

    def test_latest_query_returns_newest_rows_in_display_order(self) -> None:
        database = TelemetryDb()
        database.insert_packet(
            [(index, 0, index, float(index)) for index in range(10)], [], []
        )

        self.assertEqual(
            database.query_adc(0, limit=3),
            [(7.0, 7), (8.0, 8), (9.0, 9)],
        )
        database.close()

    def test_session_packet_count_continues_when_board_counter_restarts(self) -> None:
        database = TelemetryDb()
        history = TelemetryHistory(database)

        first = history.record(replace(sample_packet(), counter=99), 10.0, 20.0)
        second = history.record(replace(sample_packet(), counter=0), 11.0, 21.0)

        self.assertEqual(first.packet_count, 1)
        self.assertEqual(second.packet_count, 2)
        database.close()

    def test_async_file_writer_flushes_before_queries_and_shutdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = TelemetryDb(Path(directory) / "async.db", async_writes=True)
            database.insert_packet([(1, 0, 42, 2.0)], [], [])

            self.assertEqual(database.query_adc(0), [(2.0, 42)])
            database.close()

    def test_migration_adds_received_time_to_all_legacy_tables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.db"
            import sqlite3

            connection = sqlite3.connect(path)
            connection.execute("CREATE TABLE adc (ts_ms INT, slot INT, raw24 INT)")
            connection.execute("CREATE TABLE geiger (ts_ms INT, counter_id INT)")
            connection.execute("CREATE TABLE temperature (ts_ms INT, slot INT, temperature_c REAL)")
            connection.commit()
            connection.close()

            database = TelemetryDb(path)
            for table in ("adc", "geiger", "temperature"):
                columns = {
                    row[1] for row in database.conn.execute(f"PRAGMA table_info({table})")
                }
                self.assertIn("received_wall", columns)
            database.close()

    def test_archive_database_preserves_records_and_uses_unique_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "telemetry.db"
            timestamp = datetime(2026, 9, 19, 12, 34, 56)

            database = TelemetryDb(path, async_writes=True)
            database.insert_packet([(1, 0, 42, 2.0)], [], [])
            database.close()
            first_archive = archive_database(path, timestamp)

            self.assertEqual(first_archive.name, "telemetry_20260919_123456.db")
            self.assertFalse(path.exists())
            archived_database = TelemetryDb(first_archive)
            self.assertEqual(archived_database.query_adc(0), [(2.0, 42)])
            archived_database.close()

            database = TelemetryDb(path)
            database.close()
            second_archive = archive_database(path, timestamp)

            self.assertEqual(second_archive.name, "telemetry_20260919_123456_1.db")
            self.assertTrue(first_archive.exists())
            self.assertTrue(second_archive.exists())


class GeigerCsvDurabilityTests(unittest.TestCase):
    def test_full_rows_keep_a_stable_source_value(self) -> None:
        row = packet_to_row(sample_packet(), datetime.now(), ("10.0.0.4", 5005))

        self.assertEqual(row["source"], "10.0.0.4:5005")

    def test_legacy_geiger_write_is_flushed_and_counted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "geiger.csv"
            logger = TelemetryCsvLogger(path=path, mode=CSV_MODE_GEIGER_ONLY, durable=False)
            logger.start()
            logger.write_packet(sample_packet(), "127.0.0.1:5005", datetime.now())

            self.assertEqual(logger.packet_count, 1)
            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            logger.stop()

    def test_combined_geiger_packets_are_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "combined-geiger.csv"
            logger = TelemetryCsvLogger(
                path=path, mode=CSV_MODE_GEIGER_ONLY, durable=False
            )
            logger.start()
            pid = PidTelemetryPacket(3, 3, 0, 0, 100, 2, 0, 0, 0, 0, 0, 0, 0, 0, ())
            combined = CombinedTelemetryPacket(sample_packet(), pid)

            logger.write_packet(combined, "127.0.0.1:5005", datetime.now())
            logger.write_packet(combined, "127.0.0.1:5005", datetime.now())

            with path.open(newline="") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(len(rows), 1)
            self.assertEqual(logger.packet_count, 1)
            logger.stop()
