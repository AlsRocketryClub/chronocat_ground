from __future__ import annotations

from datetime import datetime
import sqlite3
from pathlib import Path
import queue
import threading
from typing import List, Tuple


DEFAULT_DATABASE_PATH = Path("chronocat_adc.db")


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

    def _migrate(self, connection: sqlite3.Connection | None = None) -> None:
        conn = self.conn if connection is None else connection
        for table in ("adc", "geiger", "temperature"):
            cursor = conn.execute(f"PRAGMA table_info({table})")
            columns = {row[1] for row in cursor.fetchall()}
            if "received_wall" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN received_wall REAL")

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
    ) -> None:
        """Persist one packet in a single transaction."""
        if self._async_writes:
            if self._closed or self._write_queue is None:
                raise RuntimeError("telemetry database is closed")
            if self._writer_error is not None:
                raise RuntimeError("telemetry database writer failed") from self._writer_error
            try:
                self._write_queue.put_nowait((adc_rows, geiger_rows, temperature_rows))
            except queue.Full as exc:
                raise RuntimeError("telemetry database writer queue is full") from exc
            return
        self._insert_packet_sync(self.conn, adc_rows, geiger_rows, temperature_rows)

    @staticmethod
    def _insert_packet_sync(
        connection: sqlite3.Connection,
        adc_rows: list[tuple],
        geiger_rows: list[tuple],
        temperature_rows: list[tuple],
    ) -> None:
        connection.executemany(
            "INSERT INTO adc (ts_ms, slot, raw24, received_wall) VALUES (?, ?, ?, ?)",
            adc_rows,
        )
        connection.executemany(
            "INSERT INTO geiger (ts_ms, received_wall, counter_id, dose_rate_cps, "
            "total_dose_sv, dose_time_sec, hv_voltage, stat_error_percent) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            geiger_rows,
        )
        connection.executemany(
            "INSERT INTO temperature (ts_ms, received_wall, slot, temperature_c) "
            "VALUES (?, ?, ?, ?)",
            temperature_rows,
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

    def query_adc(self, slot: int, cutoff_ms: int = 0, limit: int | None = None) -> List[Tuple[float, float]]:
        self.flush()
        parameters: tuple[object, ...] = (slot, cutoff_ms) if limit is None else (slot, cutoff_ms, limit)
        query = (
            "SELECT received_wall, raw24 FROM adc WHERE slot = ? AND ts_ms >= "
            "? AND received_wall IS NOT NULL ORDER BY received_wall, ts_ms"
        )
        if limit is not None:
            query = (
                "SELECT received_wall, raw24 FROM ("
                "SELECT rowid AS row_id, received_wall, raw24 FROM adc WHERE slot = ? AND ts_ms >= ? "
                "AND received_wall IS NOT NULL ORDER BY received_wall DESC, ts_ms DESC LIMIT ?"
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
            "ORDER BY received_wall, ts_ms"
        )
        if limit is not None:
            query = (
                "SELECT received_wall, dose_rate_cps FROM ("
                "SELECT rowid AS row_id, received_wall, dose_rate_cps FROM geiger WHERE counter_id = ? AND ts_ms >= ? "
                "ORDER BY received_wall DESC, ts_ms DESC LIMIT ?"
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
        self.conn.commit()
