from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import List, Tuple


class TelemetryDb:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(str(path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS adc (ts_ms INT, slot INT, raw24 INT)"
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_adc_ts ON adc(ts_ms)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_adc_slot ON adc(slot)")
        self.conn.commit()

    def insert_many(self, timestamp_ms: int, readings: List[Tuple[int, int]]) -> None:
        data = [(timestamp_ms, slot, raw24) for slot, raw24 in readings]
        self.conn.executemany(
            "INSERT INTO adc (ts_ms, slot, raw24) VALUES (?, ?, ?)", data
        )
        self.conn.commit()

    def query(
        self, slot: int, cutoff_ms: int
    ) -> List[Tuple[int, int]]:
        cursor = self.conn.execute(
            "SELECT ts_ms, raw24 FROM adc WHERE slot = ? AND ts_ms >= ? ORDER BY ts_ms",
            (slot, cutoff_ms),
        )
        return cursor.fetchall()

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM adc")
        self.conn.commit()
