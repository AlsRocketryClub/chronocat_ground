from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Tuple


class TelemetryDb:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._migrate()
        self.conn.commit()

    def _create_tables(self) -> None:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS adc (ts_ms INT, slot INT, raw24 INT, received_wall REAL)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_adc_ts ON adc(ts_ms)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_adc_slot ON adc(slot)")

        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS geiger ("
            "ts_ms INT, received_wall REAL, counter_id INT, "
            "dose_rate_cps REAL, total_dose_sv REAL, "
            "dose_time_sec INT, hv_voltage INT, stat_error_percent REAL"
            ")"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_geiger_ts ON geiger(ts_ms)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_geiger_counter ON geiger(counter_id)")

        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS temperature ("
            "ts_ms INT, received_wall REAL, slot INT, temperature_c REAL"
            ")"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_temperature_ts ON temperature(ts_ms)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_temperature_slot ON temperature(slot)")

    def _migrate(self) -> None:
        cursor = self.conn.execute("PRAGMA table_info(adc)")
        columns = {row[1] for row in cursor.fetchall()}
        if "received_wall" not in columns:
            self.conn.execute("ALTER TABLE adc ADD COLUMN received_wall REAL")

    def insert_adc(self, timestamp_ms: int, readings: List[Tuple[int, int]], received_wall: float) -> None:
        data = [(timestamp_ms, slot, raw24, received_wall) for slot, raw24 in readings]
        self.conn.executemany(
            "INSERT INTO adc (ts_ms, slot, raw24, received_wall) VALUES (?, ?, ?, ?)", data
        )
        self.conn.commit()

    def insert_geiger(self, timestamp_ms: int, counter_id: int, dose_rate_cps: float,
                      total_dose_sv: float, dose_time_sec: int, hv_voltage: int,
                      stat_error_percent: float, received_wall: float) -> None:
        self.conn.execute(
            "INSERT INTO geiger (ts_ms, received_wall, counter_id, dose_rate_cps, "
            "total_dose_sv, dose_time_sec, hv_voltage, stat_error_percent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (timestamp_ms, received_wall, counter_id, dose_rate_cps,
             total_dose_sv, dose_time_sec, hv_voltage, stat_error_percent),
        )
        self.conn.commit()

    def insert_temperature(self, timestamp_ms: int, slot: int, temperature_c: float,
                           received_wall: float) -> None:
        self.conn.execute(
            "INSERT INTO temperature (ts_ms, received_wall, slot, temperature_c) VALUES (?, ?, ?, ?)",
            (timestamp_ms, received_wall, slot, temperature_c),
        )
        self.conn.commit()

    def query_adc(self, slot: int, cutoff_ms: int = 0) -> List[Tuple[float, float]]:
        cursor = self.conn.execute(
            "SELECT received_wall, raw24 FROM adc WHERE slot = ? AND ts_ms >= ? "
            "AND received_wall IS NOT NULL ORDER BY ts_ms",
            (slot, cutoff_ms),
        )
        return cursor.fetchall()

    def query_geiger(self, counter_id: int, cutoff_ms: int = 0) -> List[Tuple[float, float]]:
        cursor = self.conn.execute(
            "SELECT received_wall, dose_rate_cps FROM geiger WHERE counter_id = ? AND ts_ms >= ? ORDER BY ts_ms",
            (counter_id, cutoff_ms),
        )
        return cursor.fetchall()

    def query_temperature(self, slot: int, cutoff_ms: int = 0) -> List[Tuple[float, float]]:
        cursor = self.conn.execute(
            "SELECT received_wall, temperature_c FROM temperature WHERE slot = ? AND ts_ms >= ? ORDER BY ts_ms",
            (slot, cutoff_ms),
        )
        return cursor.fetchall()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM adc")
        self.conn.execute("DELETE FROM geiger")
        self.conn.execute("DELETE FROM temperature")
        self.conn.commit()
