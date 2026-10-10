from __future__ import annotations

from datetime import datetime
import sqlite3
from pathlib import Path
import queue
import threading
from typing import List, Tuple


DEFAULT_DATABASE_PATH = Path("chronocat_adc.db")

# Columns added after the first release. Older databases gain them on open;
# their existing rows read NULL, which the readers treat as a good value
# (only good values were stored back then).
_ADDED_COLUMNS = {
    "adc": (("status", "INT"), ("valid", "INT")),
    "geiger": (
        ("valid", "INT"), ("error_flags", "INT"), ("event_id", "INT"),
        ("user_dose", "REAL"), ("stats_time_sec", "INT"), ("stat_cell_count", "INT"),
    ),
    "heater": (
        ("target_milli_c", "INT"), ("result", "INT"), ("pid_enabled", "INT"),
        ("manual", "INT"), ("sensor_valid", "INT"), ("proportional", "REAL"),
        ("integral", "REAL"), ("derivative", "REAL"), ("output", "REAL"),
        ("kp", "REAL"), ("ki", "REAL"), ("kd", "REAL"),
    ),
}
_ADC_COLUMNS = ("ts_ms", "slot", "raw24", "received_wall", "status", "valid")
_GEIGER_COLUMNS = (
    "ts_ms", "received_wall", "counter_id", "dose_rate_cps", "total_dose_sv",
    "dose_time_sec", "hv_voltage", "stat_error_percent", "valid", "error_flags",
    "event_id", "user_dose", "stats_time_sec", "stat_cell_count",
)
_HEATER_COLUMNS = (
    "ts_ms", "received_wall", "heater_id", "duty_permille", "target_milli_c",
    "result", "pid_enabled", "manual", "sensor_valid", "proportional", "integral",
    "derivative", "output", "kp", "ki", "kd",
)
_PACKET_COLUMNS = (
    "ts_ms", "received_wall", "counter", "version", "flags", "health_code",
    "temperature_valid_mask", "adc_valid_mask", "pid_enabled_mask", "manual_mask",
)
# Rows written before a table grew are padded with NULLs.
GOOD_ROW = "(valid IS NULL OR valid = 1)"


def _insert_sql(table: str, columns: tuple[str, ...]) -> str:
    return (
        f"INSERT INTO {table} ({', '.join(columns)}) "
        f"VALUES ({', '.join('?' for _ in columns)})"
    )


def _padded(rows, width: int) -> list[tuple]:
    return [tuple(row) + (None,) * (width - len(row)) for row in rows]


def archive_database(path: str | Path, timestamp: datetime | None = None) -> Path:
    """Checkpoint and archive a SQLite database without discarding WAL data."""
    source = Path(path)
    if not source.is_file():
        raise FileNotFoundError(f"Database not found: {source}")

    stamp = (timestamp or datetime.now()).strftime("%Y%m%d_%H%M%S")
    archive_base = source.with_name(f"{source.stem}_{stamp}")
    archive = archive_base.with_suffix(source.suffix)
    suffix = 1
    while any(
        Path(f"{archive}{sidecar_suffix}").exists()
        for sidecar_suffix in ("", "-wal", "-shm")
    ):
        archive = archive_base.with_name(f"{archive_base.name}_{suffix}").with_suffix(
            source.suffix
        )
        suffix += 1

    connection = sqlite3.connect(source)
    try:
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is not None and checkpoint[0] != 0:
            raise RuntimeError(f"Could not checkpoint database before archiving: {source}")
    finally:
        connection.close()

    source.replace(archive)
    for sidecar_suffix in ("-wal", "-shm"):
        sidecar = Path(f"{source}{sidecar_suffix}")
        if sidecar.exists():
            sidecar.replace(Path(f"{archive}{sidecar_suffix}"))
    return archive


class TelemetryDb:
    def __init__(
        self,
        path: str | Path = ":memory:",
        *,
        async_writes: bool = False,
        queue_size: int = 256,
    ) -> None:
        self.path = str(path)
        self._async_writes = async_writes and self.path != ":memory:"
        self._closed = False
        self._writer_error: Exception | None = None
        self._write_queue: queue.Queue[tuple[list[tuple], list[tuple], list[tuple]] | None] | None = None
        self._writer_thread: threading.Thread | None = None

        self.conn = sqlite3.connect(self.path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        self._migrate()
        self._create_composite_indexes()
        self.conn.commit()
        if self._async_writes:
            self._write_queue = queue.Queue(maxsize=queue_size)
            self._writer_thread = threading.Thread(
                target=self._writer_loop,
                name="telemetry-db-writer",
                daemon=True,
            )
            self._writer_thread.start()

    def _create_tables(self, connection: sqlite3.Connection | None = None) -> None:
        conn = self.conn if connection is None else connection
        conn.execute(
            "CREATE TABLE IF NOT EXISTS adc (ts_ms INT, slot INT, raw24 INT, received_wall REAL)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_adc_ts ON adc(ts_ms)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_adc_slot ON adc(slot)")

        conn.execute(
            "CREATE TABLE IF NOT EXISTS geiger ("
            "ts_ms INT, received_wall REAL, counter_id INT, "
            "dose_rate_cps REAL, total_dose_sv REAL, "
            "dose_time_sec INT, hv_voltage INT, stat_error_percent REAL"
            ")"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_geiger_ts ON geiger(ts_ms)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_geiger_counter ON geiger(counter_id)")

        conn.execute(
            "CREATE TABLE IF NOT EXISTS temperature ("
            "ts_ms INT, received_wall REAL, slot INT, temperature_c REAL"
            ")"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_temperature_ts ON temperature(ts_ms)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_temperature_slot ON temperature(slot)")

        conn.execute(
            "CREATE TABLE IF NOT EXISTS heater ("
            "ts_ms INT, received_wall REAL, heater_id INT, duty_permille INT"
            ")"
        )

        # One row per packet: what the per-sensor tables cannot show
        # (packet loss via the counter, resets, flags, health).
        conn.execute(
            "CREATE TABLE IF NOT EXISTS packet ("
            "ts_ms INT, received_wall REAL, counter INT, version INT, flags INT, "
            "health_code INT, temperature_valid_mask INT, adc_valid_mask INT, "
            "pid_enabled_mask INT, manual_mask INT"
            ")"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_packet_received ON packet(received_wall)")

        # The operator's log: commands, replies, health events, resets.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS event_log (wall REAL, kind TEXT, message TEXT)"
        )

        # Each detector's dose-rate coefficient; it never changes, so one row each.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS geiger_calibration ("
            "counter_id INT PRIMARY KEY, xder REAL NOT NULL, read_wall REAL NOT NULL"
            ")"
        )

    def _create_composite_indexes(self, connection: sqlite3.Connection | None = None) -> None:
        conn = self.conn if connection is None else connection
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_adc_slot_received "
            "ON adc(slot, received_wall, ts_ms)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_geiger_counter_received "
            "ON geiger(counter_id, received_wall, ts_ms)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_temperature_slot_received "
            "ON temperature(slot, received_wall, ts_ms)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_heater_id_received "
            "ON heater(heater_id, received_wall, ts_ms)"
        )

    def _migrate(self, connection: sqlite3.Connection | None = None) -> None:
        conn = self.conn if connection is None else connection
        for table in ("adc", "geiger", "temperature"):
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in cursor.fetchall()}
            if "received_wall" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN received_wall REAL")
        for table, added in _ADDED_COLUMNS.items():
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, kind in added:
                if name not in columns:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    def insert_adc(self, timestamp_ms: int, readings: List[Tuple[int, int]], received_wall: float) -> None:
        data = [(timestamp_ms, slot, raw24, received_wall) for slot, raw24 in readings]
        self.conn.executemany(
            "INSERT INTO adc (ts_ms, slot, raw24, received_wall) VALUES (?, ?, ?, ?)", data
        )
        self.conn.commit()

    def insert_packet(
        self,
        adc_rows: list[tuple[int, int, int, float]],
        geiger_rows: list[tuple[int, float, int, float, float, int, int, float]],
        temperature_rows: list[tuple[int, float, int, float]],
        heater_rows: list[tuple] = (),
        packet_rows: list[tuple] = (),
    ) -> None:
        """Persist one packet in a single transaction."""
        if self._async_writes:
            if self._closed or self._write_queue is None:
                raise RuntimeError("telemetry database is closed")
            if self._writer_error is not None:
                raise RuntimeError("telemetry database writer failed") from self._writer_error
            try:
                self._write_queue.put_nowait(
                    (adc_rows, geiger_rows, temperature_rows, heater_rows, packet_rows)
                )
            except queue.Full as exc:
                raise RuntimeError("telemetry database writer queue is full") from exc
            return
        self._insert_packet_sync(
            self.conn, adc_rows, geiger_rows, temperature_rows, heater_rows, packet_rows
        )

    @staticmethod
    def _insert_packet_sync(
        connection: sqlite3.Connection,
        adc_rows: list[tuple],
        geiger_rows: list[tuple],
        temperature_rows: list[tuple],
        heater_rows: list[tuple] = (),
        packet_rows: list[tuple] = (),
    ) -> None:
        connection.executemany(
            _insert_sql("adc", _ADC_COLUMNS), _padded(adc_rows, len(_ADC_COLUMNS))
        )
        connection.executemany(
            _insert_sql("geiger", _GEIGER_COLUMNS), _padded(geiger_rows, len(_GEIGER_COLUMNS))
        )
        connection.executemany(
            "INSERT INTO temperature (ts_ms, received_wall, slot, temperature_c) "
            "VALUES (?, ?, ?, ?)",
            temperature_rows,
        )
        connection.executemany(
            _insert_sql("heater", _HEATER_COLUMNS), _padded(heater_rows, len(_HEATER_COLUMNS))
        )
        connection.executemany(
            _insert_sql("packet", _PACKET_COLUMNS), _padded(packet_rows, len(_PACKET_COLUMNS))
        )
        connection.commit()

    def _writer_loop(self) -> None:
        assert self._write_queue is not None
        connection = sqlite3.connect(self.path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            self._create_tables(connection)
            self._migrate(connection)
            self._create_composite_indexes(connection)
            connection.commit()
            while True:
                batch = self._write_queue.get()
                try:
                    if batch is None:
                        return
                    self._insert_packet_sync(connection, *batch)
                except Exception as exc:
                    self._writer_error = exc
                    while True:
                        try:
                            self._write_queue.get_nowait()
                        except queue.Empty:
                            break
                        else:
                            self._write_queue.task_done()
                    return
                finally:
                    self._write_queue.task_done()
        finally:
            connection.close()

    @property
    def writer_failed(self) -> bool:
        """The background writer stopped; nothing more is being stored."""
        return self._writer_error is not None

    def flush(self) -> None:
        if self._write_queue is not None:
            self._write_queue.join()
        if self._writer_error is not None:
            raise RuntimeError("telemetry database writer failed") from self._writer_error

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

    def save_xder(self, counter_id: int, xder: float, read_wall: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO geiger_calibration (counter_id, xder, read_wall) VALUES (?, ?, ?)",
            (counter_id, xder, read_wall),
        )
        self.conn.commit()

    def insert_event(self, wall: float, kind: str, message: str) -> None:
        """One operator-log line; small and rare, so written directly."""
        self.conn.execute(
            "INSERT INTO event_log (wall, kind, message) VALUES (?, ?, ?)", (wall, kind, message)
        )
        self.conn.commit()

    def load_xder(self) -> dict[int, tuple[float, float]]:
        """Stored coefficients as {counter_id: (xder, read_wall)}."""
        rows = self.conn.execute("SELECT counter_id, xder, read_wall FROM geiger_calibration").fetchall()
        return {counter_id: (xder, read_wall) for counter_id, xder, read_wall in rows}

    def query_adc(self, slot: int, cutoff_ms: int = 0, limit: int | None = None) -> List[Tuple[float, float]]:
        self.flush()
        parameters: tuple[object, ...] = (slot, cutoff_ms) if limit is None else (slot, cutoff_ms, limit)
        query = (
            "SELECT received_wall, raw24 FROM adc WHERE slot = ? AND ts_ms >= "
            f"? AND received_wall IS NOT NULL AND {GOOD_ROW} ORDER BY received_wall, ts_ms"
        )
        if limit is not None:
            query = (
                "SELECT received_wall, raw24 FROM ("
                "SELECT rowid AS row_id, received_wall, raw24 FROM adc WHERE slot = ? AND ts_ms >= ? "
                f"AND received_wall IS NOT NULL AND {GOOD_ROW} "
                "ORDER BY received_wall DESC, ts_ms DESC LIMIT ?"
                ") ORDER BY received_wall, row_id"
            )
        cursor = self.conn.execute(
            query,
            parameters,
        )
        return cursor.fetchall()

    def query_geiger(self, counter_id: int, cutoff_ms: int = 0, limit: int | None = None) -> List[Tuple[float, float]]:
        self.flush()
        parameters: tuple[object, ...] = (counter_id, cutoff_ms) if limit is None else (counter_id, cutoff_ms, limit)
        query = (
            "SELECT received_wall, dose_rate_cps FROM geiger WHERE counter_id = ? AND ts_ms >= ? "
            f"AND {GOOD_ROW} ORDER BY received_wall, ts_ms"
        )
        if limit is not None:
            query = (
                "SELECT received_wall, dose_rate_cps FROM ("
                "SELECT rowid AS row_id, received_wall, dose_rate_cps FROM geiger WHERE counter_id = ? AND ts_ms >= ? "
                f"AND {GOOD_ROW} ORDER BY received_wall DESC, ts_ms DESC LIMIT ?"
                ") ORDER BY received_wall, row_id"
            )
        cursor = self.conn.execute(
            query,
            parameters,
        )
        return cursor.fetchall()

    def query_temperature(self, slot: int, cutoff_ms: int = 0, limit: int | None = None) -> List[Tuple[float, float]]:
        self.flush()
        parameters: tuple[object, ...] = (slot, cutoff_ms) if limit is None else (slot, cutoff_ms, limit)
        query = (
            "SELECT received_wall, temperature_c FROM temperature WHERE slot = ? AND ts_ms >= ? "
            "ORDER BY received_wall, ts_ms"
        )
        if limit is not None:
            query = (
                "SELECT received_wall, temperature_c FROM ("
                "SELECT rowid AS row_id, received_wall, temperature_c FROM temperature WHERE slot = ? AND ts_ms >= ? "
                "ORDER BY received_wall DESC, ts_ms DESC LIMIT ?"
                ") ORDER BY received_wall, row_id"
            )
        cursor = self.conn.execute(
            query,
            parameters,
        )
        return cursor.fetchall()

    def close(self) -> None:
        if self._closed:
            return
        if self._write_queue is not None:
            if self._writer_thread is not None and self._writer_thread.is_alive():
                self._write_queue.put(None)
                self._write_queue.join()
            if self._writer_thread is not None:
                self._writer_thread.join(timeout=5.0)
        self.conn.commit()
        self.conn.close()
        self._closed = True

    def clear(self) -> None:
        self.flush()
        self.conn.execute("DELETE FROM adc")
        self.conn.execute("DELETE FROM geiger")
        self.conn.execute("DELETE FROM temperature")
        self.conn.execute("DELETE FROM heater")
        self.conn.execute("DELETE FROM packet")
        self.conn.commit()
